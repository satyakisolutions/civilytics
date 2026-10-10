# backend/services/reid/clip_embedder.py
"""CLIP ViT-B/16 ONNX embedding extractor for ReID."""
import numpy as np
from PIL import Image

CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073])
CLIP_STD = np.array([0.26862954, 0.26130258, 0.27577711])


class CLIPEmbedder:
    def __init__(self, model_path: str = "/models/clip-vit-b16-visual.onnx"):
        import onnxruntime as ort
        self.session = ort.InferenceSession(
            model_path,
            providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
        )
        self.input_name = self.session.get_inputs()[0].name

    def preprocess(self, image: Image.Image) -> np.ndarray:
        img = image.convert("RGB").resize((224, 224))
        arr = np.array(img, dtype=np.float32) / 255.0
        arr = (arr - CLIP_MEAN) / CLIP_STD
        return arr.transpose(2, 0, 1)[np.newaxis].astype(np.float32)

    def embed(self, image: Image.Image) -> np.ndarray:
        inp = self.preprocess(image)
        outputs = self.session.run(None, {self.input_name: inp})
        embedding = outputs[0][0]
        return embedding / np.linalg.norm(embedding)  # 512-dim L2-normalised
