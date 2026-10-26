"""Plot supplied synthetic group labels/centers, without implementing a clustering algorithm."""
import argparse
from pathlib import Path

import numpy as np
from rich.console import Console

from . import render_image, render_text, subplots


def cluster_figure():
    rng = np.random.default_rng(7)
    centers = np.array([[-4., -1.], [0., 4.], [5., -.5]])
    points = np.concatenate([rng.normal(center, .7, (35, 2)) for center in centers])
    groups = np.repeat(['A', 'B', 'C'], 35)
    figure, axes = subplots(title='Supplied cluster results')
    axes.xlabel, axes.ylabel = 'Feature 1', 'Feature 2'
    axes.scatter(points[:, 0], points[:, 1], groups=groups, point_size=3)
    axes.scatter(centers[:, 0], centers[:, 1], label='Centers', marker='cross', color='#FFFFFF', point_size=7)
    return figure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--png', type=Path, help='save a raster preview instead of printing terminal text')
    args = parser.parse_args()
    figure = cluster_figure()
    if args.png:
        render_image(figure, (1100, 600)).save(args.png)
        print(args.png)
    else:
        console = Console()
        console.print(render_text(figure, (console.width, max(12, console.height-2))), end='')


if __name__ == '__main__':
    main()
