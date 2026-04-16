#!/usr/bin/env python3

import sys
import subprocess
import threading
import argparse
import os

# --- 配置 ---
LOG_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "mcp_io.log")
# --- 配置结束 ---

# --- 参数解析 ---
parser = argparse.ArgumentParser(
    description="Wrap a command, passing STDIN/STDOUT verbatim while logging them.",
    usage="%(prog)s <command> [args...]"
)
# 接收命令及其后续所有参数
parser.add_argument('command', nargs=argparse.REMAINDER,
                    help='The command and its arguments to execute.')

open(LOG_FILE, 'w', encoding='utf-8')

if len(sys.argv) == 1:
    parser.print_help(sys.stderr)
    sys.exit(1)

args = parser.parse_args()

if not args.command:
    print("Error: No command provided.", file=sys.stderr)
    parser.print_help(sys.stderr)
    sys.exit(1)

target_command = args.command


# --- 参数解析结束 ---

# --- I/O 转发函数 ---
# 以下函数会在独立线程中运行

def forward_and_log_stdin(proxy_stdin, target_stdin, log_file):
    """从代理标准输入读取，记录日志后写入目标标准输入。"""
    # 参数含义（对应线程 args）：
    # proxy_stdin = sys.stdin.buffer（当前脚本的输入口，数据从上游流入这里）
    # target_stdin = process.stdin（子进程的输入口，数据要被喂给它）
    try:
        while True:
            # 从当前脚本的标准输入逐行读取
            line_bytes = proxy_stdin.readline()
            if not line_bytes:  # 已到达 EOF
                break

            # 为日志解码（默认按 UTF-8，必要时可调整）
            try:
                line_str = line_bytes.decode('utf-8')
            except UnicodeDecodeError:
                line_str = f"[非 UTF-8 数据, {len(line_bytes)} 字节]\n"  # 日志中的替代展示

            # 记录日志前缀
            log_file.write(f"输入: {line_str}")
            log_file.flush()  # 确保日志及时写盘

            # 将原始字节写入目标进程标准输入
            # 也就是把当前脚本收到的输入“喂给子进程”
            target_stdin.write(line_bytes)
            target_stdin.flush()  # 确保目标进程及时接收

    except Exception as e:
        # 记录转发过程中的异常
        try:
            log_file.write(f"!!! STDIN Forwarding Error: {e}\n")
            log_f.flush()
        except:
            pass  # 若日志文件异常，避免二次报错

    finally:
        # 关键：代理输入结束时要关闭目标 stdin
        # 这样会向目标进程发送 EOF 信号（类似 shell 中 read 结束）
        try:
            target_stdin.close()
            log_file.write("--- STDIN stream closed to target ---\n")
            log_file.flush()
        except Exception as e:
            try:
                log_file.write(f"!!! Error closing target STDIN: {e}\n")
                log_file.flush()
            except:
                pass


def forward_and_log_stdout(target_stdout, proxy_stdout, log_file):
    """从目标标准输出读取，记录日志后写回代理标准输出。"""
    try:
        while True:
            # 从目标进程标准输出逐行读取
            line_bytes = target_stdout.readline()
            if not line_bytes:  # 到达 EOF（进程退出或关闭 stdout）
                break

            # 为日志进行解码
            try:
                line_str = line_bytes.decode('utf-8')
            except UnicodeDecodeError:
                line_str = f"[非 UTF-8 数据, {len(line_bytes)} 字节]\n"

            # 写入带前缀的日志
            log_file.write(f"输出: {line_str}")
            log_file.flush()

            # 将原始字节写到当前脚本标准输出
            proxy_stdout.write(line_bytes)
            proxy_stdout.flush()  # 确保输出及时可见

    except Exception as e:
        try:
            log_file.write(f"!!! STDOUT Forwarding Error: {e}\n")
            log_file.flush()
        except:
            pass
    finally:
        try:
            log_file.flush()
        except:
            pass
        # 这里不要关闭 proxy_stdout（即 sys.stdout）


# --- 主执行流程 ---
process = None
log_f = None
exit_code = 1  # 默认退出码，表示提前失败

try:
    # 以追加模式打开日志文件，供各线程写入
    log_f = open(LOG_FILE, 'a', encoding='utf-8')

    # 启动目标进程
    # stdin/stdout/stderr 都通过管道连接
    # 采用字节流并关闭缓冲（bufsize=0，readline 仍可用）
    process = subprocess.Popen(
        target_command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,  # 同时捕获标准错误
        bufsize=0  # 0 表示无缓冲二进制 I/O
    )

    # 将二进制流交给线程处理
    # sys.stdin.buffer: 当前脚本从外部接收到的“原始字节输入”入口（不是文本流）
    # process.stdin: 子进程的标准输入管道（往这里 write 就是在给子进程喂输入）
    # args 顺序映射：args[0] -> proxy_stdin，args[1] -> target_stdin，args[2] -> log_file
    stdin_thread = threading.Thread(
        target=forward_and_log_stdin,
        args=(sys.stdin.buffer, process.stdin, log_f),
        daemon=True  # 主线程退出时允许守护线程一并退出
    )

    # process.stdout: 子进程的标准输出管道；从这里读到子进程的正常输出
    # sys.stdout.buffer: 当前脚本对外输出的“原始字节出口”，可原样透传避免编码干扰
    stdout_thread = threading.Thread(
        target=forward_and_log_stdout,
        args=(process.stdout, sys.stdout.buffer, log_f),
        daemon=True
    )


    # 标准错误的转发与日志函数
    def forward_and_log_stderr(target_stderr, proxy_stderr, log_file):
        """从目标标准错误读取，记录日志后写回代理标准错误。"""
        try:
            while True:
                line_bytes = target_stderr.readline()
                if not line_bytes: break
                try:
                    line_str = line_bytes.decode('utf-8')
                except UnicodeDecodeError:
                    line_str = f"[非 UTF-8 数据, {len(line_bytes)} 字节]\n"
                log_file.write(f"STDERR: {line_str}")  # 使用 STDERR 前缀
                log_file.flush()
                proxy_stderr.write(line_bytes)
                proxy_stderr.flush()
        except Exception as e:
            try:
                log_file.write(f"!!! STDERR Forwarding Error: {e}\n")
                log_file.flush()
            except:
                pass
        finally:
            try:
                log_file.flush()
            except:
                pass


    stderr_thread = threading.Thread(
        target=forward_and_log_stderr,
        args=(process.stderr, sys.stderr.buffer, log_f),
        daemon=True
    )

    # 启动三个转发线程
    stdin_thread.start()
    stdout_thread.start()
    stderr_thread.start()  # 启动标准错误线程

    # 等待目标进程结束
    process.wait()
    exit_code = process.returncode

    # 短暂等待 I/O 线程收尾并刷新最后日志
    # 线程是守护线程，主线程退出时可能被直接结束
    # join 可让关闭过程更平滑
    # process.wait() 后子进程结束，管道通常会自然关闭
    stdin_thread.join(timeout=1.0)  # 设置超时，避免线程卡死
    stdout_thread.join(timeout=1.0)
    stderr_thread.join(timeout=1.0)


except Exception as e:
    print(f"MCP Logger Error: {e}", file=sys.stderr)
    # 尝试将主流程异常写入日志
    if log_f and not log_f.closed:
        try:
            log_f.write(f"!!! MCP Logger Main Error: {e}\n")
            log_f.flush()
        except:
            pass  # 忽略最终日志写入中的异常
    exit_code = 1  # 表示 logger 执行失败

finally:
    # 若子进程仍在运行，确保将其结束（如 logger 异常中断）
    if process and process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=1.0)  # 先给一次优雅退出机会
        except:
            pass  # 忽略清理阶段的异常
        if process.poll() is None:  # 仍未退出
            try:
                process.kill()  # 强制结束
            except:
                pass  # 忽略强杀异常

    # 关闭日志文件
    if log_f and not log_f.closed:
        try:
            log_f.close()
        except:
            pass  # 忽略最终关闭日志时的异常

    # 使用目标进程退出码退出
    sys.exit(exit_code)
