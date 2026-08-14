# Short-Time Hypocoercivity Problems

This repository is a working research notebook for questions about the
short-time behaviour of hypocoercive matrix semigroups. The central object is

\[
P(t)=e^{-Ct},
\]

where the Hermitian part of the matrix \(C\) is positive semidefinite. The
project asks how the hypocoercivity index controls the Taylor expansion of
\(\lVert P(t)\rVert_2^2\), and how the coefficients beyond the known leading
term can be determined.

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

## Building the documents

A recent TeX Live or MiKTeX installation with `latexmk` is recommended. From
the repository root, build the problem statement with:

```powershell
latexmk -pdf -cd problems/problems.tex
```

The research note and progress log can be built in the same way:

```powershell
latexmk -pdf -cd promising-approaches/rest_is_one_order_higher.tex
latexmk -pdf -cd logs/problem-1/problem1-progress.tex
```

To remove auxiliary files created next to a document, run `latexmk -c` in that
document's directory. Build products are ignored by Git.

## Current status

Problem 1 studies the higher-order short-time expansion. The research log
reports that the first requested correction is resolved: for hypocoercivity
index \(m\geq 1\), no independent \(t^{2m+2}\) term occurs. The broader task of
organising all subsequent coefficients is developed through an analytic
spectral-jet construction in the log. Readers should consult the focused note
and the research log for the precise hypotheses, proofs, and edge cases.
