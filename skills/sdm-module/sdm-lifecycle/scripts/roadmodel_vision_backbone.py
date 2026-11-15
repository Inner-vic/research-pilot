"""DINOv3 dense adapter for RoadModel's existing RGB normalized camera lattice.

No resize/crop: camera projection geometry and input normalization stay unchanged.
The ViT is frozen; new projections are trained at the downstream base LR.
"""
import hashlib
import json
import os
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F
from mmengine.registry import MODELS

DINO_SHA = 'ed8a1265847086c913535fe7d84c0156a68dc98c0edd52298b8bb4bb7bdcbbb1'


@MODELS.register_module()
class DINOv3ImageBackbone(nn.Module):
    def __init__(self, checkpoint, embed_dims=128):
        super().__init__()
        import timm
        if timm.__version__ != '1.0.22':
            raise ValueError('pinned_timm_1_0_22_required')
        path = Path(checkpoint).resolve(strict=True)
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != DINO_SHA:
                raise ValueError('dinov3_checkpoint_digest_mismatch')
        self.encoder = timm.create_model('vit_base_patch16_dinov3', pretrained=False,
                                        num_classes=0, img_size=(768, 1600))
        state = torch.load(path, map_location='cpu', weights_only=True)
        self.encoder.load_state_dict(state, strict=True)
        if not all(torch.equal(self.encoder.state_dict()[k], v) for k, v in state.items()):
            raise ValueError('dinov3_loaded_tensor_mismatch')
        self.pretrained_tensor_count = len(state)
        self.projections = nn.ModuleList([nn.Conv2d(768, embed_dims, 1) for _ in range(3)])
        self.train(True)

    def init_weights(self):
        # Pretrained tensors were strictly loaded; do not reinitialize them.
        pass

    def train(self, mode=True):
        super().train(mode)
        # BEVSegmentor.apply_train_mode unfreezes a module recursively before
        # calling train(). Reassert this nested freeze before DDP construction.
        self.encoder.requires_grad_(False)
        self.encoder.eval()
        return self

    def forward(self, images):
        n, c, h, w = images.shape
        if c != 3 or h % 32 or w % 32:
            raise ValueError('unaltered_rgb_image_lattice_must_be_multiple_of_32')
        levels = [[], [], []]
        # One camera at a time bounds attention memory without changing batch,
        # views, pixels, augmentation, projection or the downstream sample loss.
        for image in images.split(1):
            with torch.no_grad():
                features = self.encoder.forward_intermediates(
                    image, indices=(3, 7, 11), norm=True,
                    intermediates_only=True, output_fmt='NCHW')
            for i, (feature, projection, stride) in enumerate(zip(features, self.projections, (8, 16, 32))):
                projected = projection(feature)
                levels[i].append(F.interpolate(projected, size=(h // stride, w // stride),
                                                mode='bilinear', align_corners=False))
        return [torch.cat(level, dim=0) for level in levels]


def fixed_eight_score(surface):
    """Fixed class denominator; counts come from distributed surface evaluation."""
    wanted = (1, 2, 16, 17, 23, 4, 24, 25)
    ids = [int(i) for i in surface['class_indices']]
    def values(name):
        value = surface[name]
        return value.detach().cpu().tolist() if torch.is_tensor(value) else list(value)
    seen, correct, positive = (values(k) for k in ('total_seen', 'total_correct', 'total_positive'))
    ious = []
    for cls in wanted:
        i = ids.index(cls)
        union = seen[i] + positive[i] - correct[i]
        ious.append(correct[i] / union if union > 0 else 0.)
    score = 100. * sum(ious) / len(ious)
    row = {'fixed8_miou': score, 'class_indices': wanted, 'class_iou': ious,
           'raw_dynamic_miou': float(surface['miou']),
           'drivable_height_mae_cm': float(surface['drivable_height_mae_cm']),
           'drivable_count': int(surface['drivable_count']),
           'selector': 'maximum_fixed8_miou; height_reported_separately'}
    print('VISION_FIXED8 ' + json.dumps(row), flush=True)
    return score


def audit_optimizer(optimizer, model, control):
    import math
    from roadmodel_runtime_safety import atomic
    backbone = model.img_backbone
    is_dino = isinstance(backbone, DINOv3ImageBackbone)
    selected = backbone.projections if is_dino else backbone
    tracked = next(p for p in selected.parameters() if p.requires_grad)
    initial = tracked.detach().cpu().clone()
    updates = [0]
    def stepped(*_):
        updates[0] += 1
        if updates[0] not in (1, 200):
            return
        if is_dino and any(p.requires_grad or p.grad is not None for p in backbone.encoder.parameters()):
            raise ValueError('frozen_dino_encoder_received_gradient')
        gradients = [p.grad for p in selected.parameters() if p.requires_grad and p.grad is not None]
        finite = gradients and all(bool(torch.isfinite(g).all()) for g in gradients)
        nonzero = gradients and any(bool((g != 0).any()) for g in gradients)
        changed = not torch.equal(initial, tracked.detach().cpu())
        if not finite or not nonzero or (updates[0] == 200 and not changed):
            raise ValueError('vision_training_gradient_or_update_missing')
        row = dict(rank=int(os.environ.get('RANK', '0')), successful_update=updates[0],
                   encoder_frozen=is_dino, gradient_finite=bool(finite), gradient_nonzero=bool(nonzero),
                   selected_parameter_changed=changed, pretrained_tensor_count=getattr(backbone, 'pretrained_tensor_count', None))
        atomic(Path(control)/('vision-audit-u'+str(updates[0])+'-rank-'+str(row['rank'])+'.json'), row)
        print('VISION_GRADIENT_AUDIT '+json.dumps(row),flush=True)
    underlying = getattr(optimizer, 'optimizer', optimizer)
    return underlying.register_step_post_hook(stepped)


def gpu_preflight(checkpoint, output):
    """Actual original-resolution adapter probe inside the assigned task."""
    torch.manual_seed(42)
    model = DINOv3ImageBackbone(checkpoint).cuda()
    model.requires_grad_(True)
    model.train()
    initial = model.projections[0].weight.detach().clone()
    optimizer = torch.optim.AdamW(model.projections.parameters(), lr=2e-5)
    with torch.autocast('cuda', dtype=torch.float16):
        features = model(torch.randn(1, 3, 768, 1600, device='cuda'))
        loss = sum(x.float().square().mean() for x in features)
    if [list(x.shape) for x in features] != [[1, 128, 96, 200], [1, 128, 48, 100], [1, 128, 24, 50]]:
        raise ValueError('full_resolution_feature_lattice_mismatch')
    if not torch.isfinite(loss):
        raise ValueError('full_resolution_nonfinite_features')
    loss.backward()
    if any(p.grad is not None or p.requires_grad for p in model.encoder.parameters()):
        raise ValueError('preflight_encoder_not_frozen')
    if not all(p.grad is not None and bool(torch.isfinite(p.grad).all()) for p in model.projections.parameters()):
        raise ValueError('preflight_projection_gradient_missing_or_nonfinite')
    optimizer.step()
    if torch.equal(initial, model.projections[0].weight):
        raise ValueError('preflight_projection_parameter_unchanged')
    torch.cuda.synchronize()
    result = {'passed': True, 'input_shape': [1, 3, 768, 1600],
              'output_shapes': [list(x.shape) for x in features], 'pretrained_sha256': DINO_SHA,
              'finite_backward_and_projection_update': True, 'encoder_frozen': True,
              'peak_allocated_bytes': torch.cuda.max_memory_allocated(),
              'scope': 'Single camera adapter GPU integration; full batch32rank training fit checked by actual in-task updates'}
    Path(output).write_text(json.dumps(result) + '\n')
    print('VISION_GPU_PREFLIGHT ' + json.dumps(result), flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    gpu_preflight(args.checkpoint, args.output)
