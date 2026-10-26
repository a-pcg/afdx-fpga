# Historical exploratory entry; use the fresh, checked runner for replay.
import sys
from verify_timing import main
if __name__ == '__main__':
    if '--probe' not in sys.argv:
        sys.argv += ['--probe', 'ram']
    main()
