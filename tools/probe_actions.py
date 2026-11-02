"""Count action subtypes across replays."""
import os
import sys
import collections

from mgz.fast import header as fast_header
from mgz import fast


def main(paths):
    tally = collections.Counter()
    for path in paths:
        print(os.path.basename(path))
        with open(path, 'rb') as d:
            eof = os.fstat(d.fileno()).st_size
            fast_header.parse(d)
            fast.meta(d)
            while d.tell() < eof:
                o = fast.operation(d)
                if o[0] == fast.Operation.ACTION:
                    a = o[1]
                    tally[a[0].name] += 1
    for k, v in tally.most_common():
        print(f"  {k:16s} {v}")


if __name__ == "__main__":
    main(sys.argv[1:])
