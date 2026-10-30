---
license: mit
tags:
- code
---

# Dataset card


The Luau dataset is a collection of code fragments collected from the Roblox Luau Data Sharing program.

Only experiences where creators gave us permission to contribute to the public Luau Dataset were used for producing this dataset.

# Languages:
Lua, Luau

# License:
MIT

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the “Software”), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

# Format:
The dataset format is in jsonl format, with prompt / completion fields.

# Dataset usage:
This dataset is designed for fine tuning large language models.

# Risks:

The dataset has been filtered for various quality signals, though Roblox makes no guarantees of data quality.

# Evaluation:

We have found that typically fine tuning a generalist code LLM improve it’s performance on Roblox Lua code quality by 10 to 20%.
