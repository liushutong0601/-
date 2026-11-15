import argparse

from autoquiz.gui import run_app


def check_runtime_dependencies() -> None:
    """无网络自检：验证 AI SDK 能创建客户端，Playwright 驱动能启动。"""
    from autoquiz.kimi import DeepSeekSolver, KimiSolver

    for solver_class in (KimiSolver, DeepSeekSolver):
        client = solver_class(api_key="dependency-check")._client()
        client.close()

    from playwright.sync_api import sync_playwright

    manager = sync_playwright().start()
    manager.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="学习通章节测验自动答题")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dependency-check", action="store_true", help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.dependency_check:
        check_runtime_dependencies()
    else:
        run_app(smoke_test=arguments.smoke_test)
