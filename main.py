import sys

from core.pipeline import LocationPipeline


BANNER = r"""
 __   __  ___   _   _  ____      _   _  _____ __  __ _____     _       ___    ____     _     _____  ___   ___   _   _
 \ \ / / / _ \ | | | ||  _ \    | \ | || ____|\ \/ /|_   _|   | |     / _ \  / ___|   / \   |_   _||_ _| / _ \ | \ | |
  \ V / | | | || | | || |_) |   |  \| ||  _|   \  /   | |     | |    | | | || |      / _ \    | |   | | | | | ||  \| |
   | |  | |_| || |_| ||  _ <    | |\  || |___  /  \   | |     | |___ | |_| || |___  / ___ \   | |   | | | |_| || |\  |
   |_|   \___/  \___/ |_| \_\   |_| \_||_____|/_/\_\  |_|     |_____| \___/  \____|/_/   \_\  |_|  |___| \___/ |_| \_|

                             YOUR NEXT LOCATION
"""


def main():

    print(BANNER)

    # An optional destination can be passed on the command line, e.g.
    #
    #     python main.py "New York City, USA"
    #
    # Without one, the AI picks a destination itself.
    instruction = " ".join(
        sys.argv[1:]
    ).strip() or None

    pipeline = LocationPipeline()

    pipeline.create_episode(
        instruction=instruction
    )


if __name__ == "__main__":

    main()