---
library_name: transformers.js
base_model:
- cointegrated/rubert-tiny-sentiment-balanced
---

# rubert-tiny-sentiment-balanced (ONNX)

This is an ONNX version of [cointegrated/rubert-tiny-sentiment-balanced](https://huggingface.co/cointegrated/rubert-tiny-sentiment-balanced). It was automatically converted and uploaded using [this space](https://huggingface.co/spaces/onnx-community/convert-to-onnx).

## Usage (Transformers.js)

If you haven't already, you can install the [Transformers.js](https://huggingface.co/docs/transformers.js) JavaScript library from [NPM](https://www.npmjs.com/package/@huggingface/transformers) using:
```bash
npm i @huggingface/transformers
```

**Example:** Sentiment Classification.

```js
import { pipeline } from '@huggingface/transformers';

const classifier = await pipeline('text-classification', 'onnx-community/rubert-tiny-sentiment-balanced-ONNX');
const output = await classifier('I love transformers!');
```