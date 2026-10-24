"""The Arena: reliability tests for models, checked by code and never by another model.

A suite of questions is built from a seed (``suite``), each with a verifier that decides by parsing,
comparing, executing or validating the answer (``verifiers``). A run puts several models through the same
questions, one at a time in memory (``runner``), and the results are kept in the database (``store``) and
turned into scores and comparisons (``scoring``). What no verifier can decide stays *not scored*: it is never
counted as an error and never judged by a model.

Import what you need from the submodules; this package imports nothing by itself.
"""

SUITE_ID = "willitrun-arena"
# Bump it whenever a question, a verifier or the way an answer is read changes: results of two versions are not comparable.
SUITE_VERSION = "1.0.0"
