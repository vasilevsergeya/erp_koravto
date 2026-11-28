import { createWorker, OEM, PSM } from "tesseract.js";

// The viewer owns this worker and its child, including while language data is loading.
self.onmessage = async ({ data }: MessageEvent<Blob>) => {
  try {
    const worker = await createWorker(["eng", "rus", "kor"], OEM.LSTM_ONLY, {
      workerPath: "/ocr/worker.min.js",
      workerBlobURL: false,
      corePath: "/ocr",
      langPath: "/ocr",
      errorHandler: () => self.postMessage({ error: true }),
    });
    await worker.setParameters({ tessedit_pageseg_mode: PSM.AUTO });
    const result = await worker.recognize(data);
    self.postMessage({ text: result.data.text.trim() });
  } catch {
    self.postMessage({ error: true });
  }
};
