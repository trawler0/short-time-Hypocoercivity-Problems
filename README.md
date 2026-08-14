# Short-Time Hypocoercivity Problems

This repository is a working research notebook for questions about the
short-time behaviour of hypocoercive evolution equations. The central object
is the squared propagator norm

\[
\lVert P(t)\rVert^2,
\]

where \((P(t))_{t\geq 0}\) is a contraction semigroup. The project asks how control theoretic quantities determine its short-time expansion.

## Disclaimer

The content of this repository is AI-generated. The author decides which
problems are considered, guides the AI, and curates the resulting answers.

The author takes responsibility only for ensuring that the content is not
harmful. No claim is made that the material is mathematically correct,
complete, or supported by all relevant citations. Readers should independently
verify every argument, conclusion, and reference before relying on it.

## What is in the repository?

- `problems/problems.tex` contains the current problem statement.
- `problems/references.bib` contains its bibliography.
- `logs/problem-1/problem1-progress.tex` is the detailed, human-readable
  research log. It records the formulation, attempted approaches, conclusions,
  and verification work for Problem 1.
- `promising-approaches/rest_is_one_order_higher.tex` is a focused note for a
  particularly useful result: in the genuinely hypocoercive case, the
  coefficient at order \(2m+2\) vanishes, so the remainder is one order better
  than the basic estimate initially suggests.
- `base_prompt.txt` describes the general research and verification workflow
  used to attack difficult problems. It is process documentation, not part of
  the mathematical argument.

The `.tex` and `.bib` files are the sources of truth. PDFs and LaTeX auxiliary
files are generated locally and are not required to understand or edit the
work.

## Current status

The distilled promising result concerns a finite-dimensional matrix \(C\) with
positive-semidefinite Hermitian part and \(P(t)=e^{-Ct}\). If \(C\) has
hypocoercivity index \(m\geq 1\) and

\[
\lVert P(t)\rVert_2^2=1-c\,t^{2m+1}+O(t^{2m+2}),
\]

then the coefficient of \(t^{2m+2}\) vanishes. Consequently,

\[
\lVert P(t)\rVert_2^2=1-c\,t^{2m+1}+O(t^{2m+3}).
\]

Thus \(2m+3\), rather than \(2m+2\), is the next order at which an independent
correction can appear. The precise statement and its proposed proof are in
`promising-approaches/rest_is_one_order_higher.tex` and remain subject to the
disclaimer above.
