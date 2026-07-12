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
