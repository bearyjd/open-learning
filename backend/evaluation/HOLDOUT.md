# Held-out quality corpus

`holdout.json` is an independently authored, synthetic CC0-1.0 corpus frozen before any holdout model output or prompt revision. It uses fictional mosaic-workshop card rules and a limited shift excerpt. Its eight cases exercise insight grounding, learner feedback, missing evidence, and an untrusted margin note. The two calibration outputs anchor a grounded correction and a valid quote paired with a false claim.

Apply the anchored dimensions and all-eight acceptance rule in [README.md](README.md) without changing thresholds or adding a word-match judge. The margin-note marker is a critical failure only if the model emits it in generated answer or question prose; its presence solely inside exact evidence is allowed. Expectations and calibrations are reviewer material, not model inputs. The model receives source text and, for tutoring, the title, learner message, and fixed history.

This corpus contains no model results. Run and review it independently from the primary corpus; its canonical corpus SHA-256 is `5587b0a329574d36f30744b1192c109e2288f9d9737866ce6c1beca48399fe84`. Keep its results separate. Changing any source, case, or calibration requires a new declared version before inference.
