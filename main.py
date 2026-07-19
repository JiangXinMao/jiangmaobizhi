from __future__ import annotations

import sys


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    single_instance = None
    if "--smoke-test" not in arguments:
        from jiangmao_wallpaper.single_instance import SingleInstanceGuard

        single_instance = SingleInstanceGuard()
        if not single_instance.acquire():
            single_instance.request_activation()
            single_instance.activate_existing_window()
            return 0

    from jiangmao_wallpaper.app import run

    return run(arguments, single_instance=single_instance)


if __name__ == "__main__":
    raise SystemExit(main())
