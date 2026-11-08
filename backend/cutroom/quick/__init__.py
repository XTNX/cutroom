"""Quick mode: a declarative block timeline with per-block render cache.

The project is a list of blocks on four tracks (video, audio, slides, overlays); positions are in
project frames, source times in seconds. Every block's render is cached by a hash of what it shows,
so an edit rebuilds only the blocks it touches and the export is a stream-copy concat.
"""
