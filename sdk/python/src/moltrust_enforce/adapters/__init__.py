"""Adapters that put `POST /enforce/check` in front of a third-party tool surface.

An adapter turns a tool call into a mandate transaction, asks for a verdict and runs the
caller's own function only when the verdict lets it through. It ships no client for the
third party: the caller hands over the call as a callable and keeps its own credentials.
"""
