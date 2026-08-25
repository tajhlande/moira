# EVAL_LOG

Scores are recorded manually via `moira_eval.log`.

---

## 2026-07-12 batch (commit a5aea2a6, main)

| Question                   | Rubric  | Score | web_search | Status |
|----------------------------|---------|-------|------------|--------|
| flaming-hot-cheetos        | general | 21/25 | 8          | PASS   |
| future-nostalgia           | general | 18/25 | 19         | PASS   |
| jazz-trumpeters            | general | 15/25 | 19         | FAIL   |
| telescope-mount-cost       | general | 17/25 | 18         | PASS   |
| trade-policy-manufacturing | general | 17/25 | 24         | PASS   |
| tyranitar-ou               | pokemon | 10/16 | 12         | FAIL   |
| water-blood-pressure       | general | 14/25 | 21         | FAIL   |

- Agent model: z-ai/glm-5.2-20260616
- Note: Baseline after SearXNG en-US fix + hard-fail thresholds. 3/7 PASS, avg 16/25 (64%). FAILs: jazz-trumpeters (Critique quality <=2), tyranitar-ou (total 10/16, 3 hard-fail categories), water-blood-pressure (Citation support + Goal alignment <=2, total 14/25).

## 2026-07-17 batch (commit a848b0b2, cleanup-empty-claims)

| Question                   | Rubric  | Score | web_search | Status |
|----------------------------|---------|-------|------------|--------|
| flaming-hot-cheetos        | general | 16/25 | 27         | PASS   |
| future-nostalgia           | general | 11/25 | 21         | FAIL   |
| jazz-trumpeters            | general | 9/25  | 26         | FAIL   |
| telescope-mount-cost       | general | 13/25 | 19         | FAIL   |
| trade-policy-manufacturing | general | 17/25 | 19         | FAIL   |
| tyranitar-ou               | pokemon | 13/16 | 12         | PASS   |
| water-blood-pressure       | general | 15/25 | 21         | FAIL   |

- Agent model: z-ai/glm-5.2
- Note: Added check to mark facts as unverified if the agent's fact statement says that it could not be verified

## 2026-07-18 batch (commit bc94889d, cleanup-empty-claims)

| Question                   | Rubric  | Score | web_search | Status |
|----------------------------|---------|-------|------------|--------|
| flaming-hot-cheetos        | general | 16/25 | 9          | FAIL   |
| future-nostalgia           | general | 16/25 | 10         | PASS   |
| jazz-trumpeters            | general | 13/25 | 10         | FAIL   |
| telescope-mount-cost       | general | 14/25 | 10         | FAIL   |
| trade-policy-manufacturing | general | 15/25 | 10         | FAIL   |
| tyranitar-ou               | pokemon | 9/16  | 10         | FAIL   |
| water-blood-pressure       | general | 16/25 | 10         | FAIL   |

- Agent model: z-ai/glm-5.2
- Note: Added per-step tool calling limits

==============================================
Evals below this point have a bug:
34 out of 106 workflow runs (32%) were scored against the wrong run — not the most recent completed run at the time.
==============================================



## 2026-07-20 batch (commit 8105cc0b, main)

| Question                   | Rubric  | Score | web_search | Status |
|----------------------------|---------|-------|------------|--------|
| flaming-hot-cheetos        | general | 16/25 | 10         | PASS   |
| future-nostalgia           | general | 16/25 | 10         | FAIL   |
| jazz-trumpeters            | general | 18/25 | 10         | FAIL   |
| telescope-mount-cost       | general | 18/25 | 10         | FAIL   |
| trade-policy-manufacturing | general | 19/25 | 10         | FAIL   |
| tyranitar-ou               | pokemon | 7/16  | 10         | FAIL   |
| water-blood-pressure       | general | 18/25 | 10         | FAIL   |

- Agent model: z-ai/glm-5.2
- Note: Completed most improvements from fact-extraction-and-claim-quality.md .

## 2026-07-21 batch (commit 616cad46, main)

| Question                   | Rubric  | Score | web_search | Status |
|----------------------------|---------|-------|------------|--------|
| flaming-hot-cheetos        | general | 17/25 | 10         | PASS   |
| future-nostalgia           | general | 17/25 | 10         | PASS   |
| jazz-trumpeters            | general | 17/25 | 10         | FAIL   |
| telescope-mount-cost       | general | 17/25 | 10         | FAIL   |
| trade-policy-manufacturing | general | 16/25 | 10         | FAIL   |
| tyranitar-ou               | pokemon | 11/16 | 10         | FAIL   |
| water-blood-pressure       | general | 11/25 | 10         | FAIL   |

- Agent model: z-ai/glm-5.2
- Note: Fixed url_content passing to research step to send 15000 chars, not 500

## 2026-07-22 batch (commit 23c462e6, conclusion-inference)

| Question                   | Rubric  | Score | web_search | Status |
|----------------------------|---------|-------|------------|--------|
| flaming-hot-cheetos        | general | 15/25 | 10         | FAIL   |
| future-nostalgia           | general | 16/25 | 10         | PASS   |
| jazz-trumpeters            | general | 16/25 | 10         | PASS   |
| telescope-mount-cost       | general | 17/25 | 10         | PASS   |
| trade-policy-manufacturing | general | 19/25 | 10         | FAIL   |
| tyranitar-ou               | pokemon | 11/16 | 10         | FAIL   |
| water-blood-pressure       | general | 18/25 | 10         | PASS   |

- Agent model: z-ai/glm-5.2
- Note: Added inference as a method for drawing conclusions.
        `jazz-trumpeters` is an anomaly here - report was thorough but drew on citation snippets, not any of the facts or conclusions as all were unverified.

==============================================
Evals above this point have a bug:
34 out of 106 workflow runs (32%) were scored against the wrong run — not the most recent completed run at the time.
==============================================

## 2026-08-11 batch (commit 7d024788, main)

| Question                   | Rubric  | Score | web_search | Status |
|----------------------------|---------|-------|------------|--------|
| flaming-hot-cheetos        | general | 21/25 | 10         | PASS   |
| future-nostalgia           | general | 12/25 | 10         | FAIL   |
| jazz-trumpeters            | general | 16/25 | 10         | FAIL   |
| telescope-mount-cost       | general | 18/25 | 10         | PASS   |
| trade-policy-manufacturing | general | 20/25 | 10         | PASS   |
| tyranitar-ou               | pokemon | 14/16 | 10         | PASS   |
| water-blood-pressure       | general | 19/25 | 5          | PASS   |

- Agent model: z-ai/glm-5.2
- Note: This batch was run against Qwen3.6-35B-A3B-MTP-GGUF in the UD-Q8_K_XL quant
  (as opposed to our usual UD-Q4_K_M quant) to see if the higher quant made any difference
  in quality. Though there is some difference, its not enough to do all the time, and this
  benchmark took over 2 1/2 hours to run.  It does, however, point towards
  possible future work in decomposing the verification tasks into
  distinct sub-tasks for each conclusion. More thought about task decomposition is needed.

## 2026-08-24 batch (commit cdc7a27b, planning-freedom)

| Question | Rubric | Score | web_search | Status |
|----------|--------|-------|------------|--------|
| flaming-hot-cheetos | general | 18/25 | 10 | PASS |
| future-nostalgia | general | 15/25 | 10 | FAIL |
| jazz-trumpeters | general | 17/25 | 10 | PASS |
| telescope-mount-cost | general | 21/25 | 10 | PASS |
| trade-policy-manufacturing | general | 16/25 | 10 | FAIL |
| tyranitar-ou | pokemon | 9/16 | 8 | FAIL |
| water-blood-pressure | general | 18/25 | 10 | PASS |

- Agent model: z-ai/glm-5.2
- Note: Re-evaluating the planning-freedom branch

## 2026-08-24 batch (commit d0dcd2fb, planning-freedom)

| Question | Rubric | Score | web_search | Status |
|----------|--------|-------|------------|--------|
| flaming-hot-cheetos | general | 19/25 | 10 | PASS |
| future-nostalgia | general | 17/25 | 10 | PASS |
| jazz-trumpeters | general | 15/25 | 10 | FAIL |
| telescope-mount-cost | general | 15/25 | 10 | FAIL |
| trade-policy-manufacturing | general | 17/25 | 10 | PASS |
| tyranitar-ou | pokemon | 12/16 | 7 | FAIL |
| water-blood-pressure | general | 21/25 | 10 | PASS |

- Agent model: z-ai/glm-5.2
- Note: Evaluating phase 2 of planning-freedom branch
