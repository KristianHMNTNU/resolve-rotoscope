"""Tracking a mask through the clip with Cutie (PyTorch 2.2, CPU)."""

import logging
import os
import sys
import time

import numpy as np
import torch

from . import contrast
from .config import CUTIE_DIR, MODELS_DIR

log = logging.getLogger(__name__)


def _import_cutie():
    if CUTIE_DIR not in sys.path:
        sys.path.insert(0, CUTIE_DIR)
    from cutie.model.utils import resnet

    # The fine-tuned weights replace the backbone anyway; skip the ImageNet download.
    if not getattr(resnet, "_rotoscope_patched", False):
        r18, r50 = resnet.resnet18, resnet.resnet50
        resnet.resnet18 = lambda pretrained=True, **kw: r18(False, **kw)
        resnet.resnet50 = lambda pretrained=True, **kw: r50(False, **kw)
        resnet._rotoscope_patched = True


class Tracker:
    def __init__(self, variant="base", threads=8):
        _import_cutie()
        from hydra import compose, initialize_config_dir
        from cutie.inference.utils.args_utils import get_dataset_cfg
        from cutie.model.cutie import CUTIE

        torch.set_grad_enabled(False)
        torch.set_num_threads(threads)
        with initialize_config_dir(version_base="1.3.2", config_dir=os.path.join(CUTIE_DIR, "cutie", "config")):
            cfg = compose(config_name="eval_config", overrides=[f"model={variant}"])
        get_dataset_cfg(cfg)
        self.cfg = cfg
        self.net = CUTIE(cfg).eval()
        weights = torch.load(os.path.join(MODELS_DIR, "cutie", f"cutie-{variant}-mega.pth"), map_location="cpu")
        self.net.load_weights(weights)
        self.variant = variant

    def track(self, frames, lut, seed_index, seed_mask, indices, internal_size, progress=None, cancel=None):
        """Propagate seed_mask (uint8 0..255, at frame size) from frames[seed_index]
        over `indices` (in order). Yields (index, prob uint8 0..255) for each frame.
        """
        from cutie.inference.inference_core import InferenceCore

        proc = InferenceCore(self.net, cfg=self.cfg)
        proc.max_internal_size = internal_size

        def to_tensor(f):
            return torch.from_numpy(contrast.apply(f, lut)).permute(2, 0, 1).float() / 255

        # Grad mode is per thread, and tracking runs in a worker thread.
        with torch.inference_mode():
            mask = torch.from_numpy((seed_mask > 127).astype(np.int64))
            proc.step(to_tensor(frames[seed_index]), mask, objects=[1])
            t0 = time.perf_counter()
            for n, i in enumerate(indices):
                if cancel and cancel():
                    return
                prob = proc.step(to_tensor(frames[i]), end=(n == len(indices) - 1))
                p = prob[1] if prob.shape[0] > 1 else torch.zeros_like(prob[0])
                yield i, (p.detach().clamp(0, 1).numpy() * 255).astype(np.uint8)
                if progress:
                    el = time.perf_counter() - t0
                    progress(n + 1, len(indices), el / (n + 1))
