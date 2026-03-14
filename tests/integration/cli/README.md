# cli/

Real CLI smoke tests that execute `python -m terok.cli` against isolated
config/state roots. These verify top-level help and command discovery
without mocking the parser or dispatch layer.
