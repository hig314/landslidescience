"""Check a faces.py run against the months Hig has judged (a regression gate
for tuning the face detector: a change that loses a face Hig called clear,
or lets back one Hig called wrong, should be seen, not found later).

    python3 judge.py NAME            # reads OUT/NAME/faces/faces.geojson

Add a month here whenever Hig judges one.
"""
import json, os, sys

OUT = '/Volumes/Nunatak/glacier_s1_build'
# name: (months that should have a face, months that should not)
JUDGED = {
    # Hig 2026-11-24: Nov 2017 bright ice on darker water and Feb 2024 a
    # bright line, both clear; Jul 2018 a plume, Aug 2023 and Jul 2025
    # along floating ice (Jul 2025 may come back if it lands on the front)
    'south_sawyer': (['2017-11', '2024-02'], ['2018-07', '2023-08', '2020-11']),
    # Hig 2026-11-23: Dec 2019 and Jul 2020 very clear, Mar/Apr 2020
    # indistinct; Hig's traces 2017-02, 2018-06, 2019-04
    'endicott': (['2019-12', '2020-07', '2017-02', '2018-06', '2019-04'], ['2020-03', '2020-04']),
}


def main():
    n = sys.argv[1]
    F = {f['properties']['month']: f['properties']['method']
         for f in json.load(open(os.path.join(OUT, n, 'faces', 'faces.geojson')))['features']}
    keep, drop = JUDGED.get(n, ([], []))
    miss = [m for m in keep if m not in F]
    bad = [m for m in drop if m in F]
    print('%s: %d kept | wanted, missing: %s | unwanted, present: %s' % (
        n, len(F), ', '.join(miss) or 'none', ', '.join(bad) or 'none'))


if __name__ == '__main__':
    main()
