# Judge blindness: per-turn LLM judge versus a one-line state post-condition

Sessions: 600 (phantom transition 200, control (genuine interruption) 200, bad recovery (positive control) 200). Rates are percentages with 95 percent Wilson intervals in brackets. TF = task fulfilment, RQ = recovery quality, both 1 to 5. "Flag: yes/no" is the fraction answering yes to "was the caller moved forward in the call flow without the assistant finishing the step that justifies it?". "Flag: free text" is the fraction giving any answer other than "nothing" to "did anything go wrong in this interaction?". "Post-condition" is the fraction of the same sessions the state post-condition marks as a phantom transition.

| Judge | Arm | N | TF mean | RQ mean | RQ = 5 | Flag: yes/no | Flag: free text | Flag: either | Post-condition |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| claude-haiku-4-5 via Claude Code subagent | phantom transition | 200 | 2.44 | 4.36 | 64.5% | 73.5% [67.0, 79.1] | 72.0% | 74.5% | 100.0% [98.1, 100.0] |
| claude-haiku-4-5 via Claude Code subagent | control (genuine interruption) | 200 | 4.40 | 4.95 | 95.5% | 0.0% [0.0, 1.9] | 3.5% | 3.5% | 0.0% [0.0, 1.9] |
| claude-haiku-4-5 via Claude Code subagent | bad recovery (positive control) | 200 | 1.66 | 1.08 | 0.0% | 4.0% [2.0, 7.7] | 100.0% | 100.0% | 0.0% [0.0, 1.9] |
| mlx-community/Qwen2.5-0.5B-Instruct-4bit via mlx_lm 0.31.3 (local) | phantom transition | 104 | 5.00 | 5.00 | 100.0% | 100.0% [96.4, 100.0] | 0.0% | 100.0% | 100.0% [96.4, 100.0] |
| mlx-community/Qwen2.5-0.5B-Instruct-4bit via mlx_lm 0.31.3 (local) | control (genuine interruption) | 74 | 5.00 | 5.00 | 100.0% | 100.0% [95.1, 100.0] | 0.0% | 100.0% | 0.0% [0.0, 4.9] |
| mlx-community/Qwen2.5-0.5B-Instruct-4bit via mlx_lm 0.31.3 (local) | bad recovery (positive control) | 68 | 4.88 | 4.94 | 98.5% | 100.0% [94.7, 100.0] | 0.0% | 100.0% | 0.0% [0.0, 5.3] |

## Phantom arm: detection by injected transition (yes/no question)

| Judge | GREETING->DISCOVERY | DISCOVERY->PITCH (0 answers) | DISCOVERY->PITCH (1 answer) | PITCH->CLOSE |
|---|---:|---:|---:|---:|
| claude-haiku-4-5 via Claude Code subagent | 26.0% (n=50) | 93.8% (n=48) | 77.8% (n=54) | 97.9% (n=48) |
| mlx-community/Qwen2.5-0.5B-Instruct-4bit via mlx_lm 0.31.3 (local) | 100.0% (n=7) | 100.0% (n=30) | 100.0% (n=38) | 100.0% (n=29) |

## Phantom arm: detection by interruption type (yes/no question)

| Judge | correction | normal | pushback | topic_switch |
|---|---:|---:|---:|---:|
| claude-haiku-4-5 via Claude Code subagent | 89.7% (n=29) | 66.7% (n=54) | 70.0% (n=60) | 75.4% (n=57) |
| mlx-community/Qwen2.5-0.5B-Instruct-4bit via mlx_lm 0.31.3 (local) | 100.0% (n=20) | 100.0% (n=17) | 100.0% (n=43) | 100.0% (n=24) |
