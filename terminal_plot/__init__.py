"""Reusable terminal plotting: supplied data + Figure/Axes instructions, without fetching data."""
from .model import Axes, Figure, Guide, Series, snapshot, subplots
from .render import render_image, render_text

__all__ = ['Axes', 'Figure', 'Guide', 'Series', 'snapshot', 'subplots', 'render_image', 'render_text']
