"""Final-layer local feature readout; no GT enters the inference path."""
import torch
import weakref
from torch import nn
from torch.utils.checkpoint import checkpoint
INSTANCES = weakref.WeakSet()


class FeatureSemanticResidual(nn.Module):
    def __init__(self, feature_dim=128, num_classes=27, hidden_dim=128,
                 neighbors=8, radius=0.05, query_chunk=512, checkpoint_chunks=True):
        super().__init__()
        if min(feature_dim, num_classes-1, hidden_dim, neighbors, query_chunk) < 1 or radius <= 0:
            raise ValueError('positive_residual_dimensions_required')
        self.feature_dim, self.num_classes = feature_dim, num_classes
        self.neighbors, self.radius, self.query_chunk = neighbors, radius, query_chunk
        self.checkpoint_chunks = bool(checkpoint_chunks)
        attribute_dim = 3 + 3 + 4 + 1 + num_classes
        # New parameters must not shift the original data/dropout RNG stream.
        with torch.random.fork_rng(devices=[]):
            torch.random.default_generator.manual_seed(42)
            self.value = nn.Sequential(nn.Linear(feature_dim+attribute_dim, hidden_dim), nn.SiLU(),
                                       nn.Linear(hidden_dim, hidden_dim), nn.SiLU())
            self.readout = nn.Sequential(nn.Linear(hidden_dim+num_classes, hidden_dim), nn.SiLU(),
                                         nn.Linear(hidden_dim, num_classes-1))
            nn.init.zeros_(self.readout[-1].weight)
            nn.init.zeros_(self.readout[-1].bias)
        self.last_diagnostics = {}
        self.gradient_max = {}
        INSTANCES.add(self)
        self.readout[-1].weight.register_hook(lambda g:self._record_gradient('residual_projection',g))
        self.value[0].weight.register_hook(lambda g:self._record_gradient('residual_value_mlp',g))

    def _record_gradient(self, name, gradient):
        value=gradient.detach().abs().amax()
        self.gradient_max[name]=torch.maximum(self.gradient_max.get(name,torch.zeros_like(value)),value)
        return gradient

    def runtime_snapshot(self):
        return {**self.last_diagnostics,'gradient_abs_max':{k:float(v.cpu()) for k,v in self.gradient_max.items()},
                'projection_weight_abs_max':float(self.readout[-1].weight.detach().abs().max().cpu())}

    @staticmethod
    def base_probabilities(probabilities):
        nonempty = probabilities[..., :-1].float()
        mass = nonempty.sum(-1, keepdim=True)
        # Define the zero-mass path before division, keeping occupied supervision.
        floor = 1e-8 * mass / nonempty.shape[-1]
        bounded = torch.maximum(nonempty, floor)
        normalized = bounded / bounded.sum(-1, keepdim=True).clamp_min(torch.finfo(torch.float32).tiny)
        return torch.where(mass > 0, normalized, torch.full_like(normalized, 1/nonempty.shape[-1]))

    def _chunk(self, query, means, scales, rotations, opacities, semantics, features, base, occupancy):
        batch, count, _ = query.shape
        k = min(self.neighbors, means.shape[1])
        # Selection is discrete; retain gradients through the selected geometry.
        with torch.no_grad():
            distance = torch.cdist(query.float(), means[..., :2].float()).square()
            indices = distance.topk(k, dim=-1, largest=False, sorted=True).indices
        batch_index = torch.arange(batch, device=query.device)[:, None, None]
        selected_means = means[batch_index, indices]
        relative = selected_means - torch.cat([query, torch.zeros_like(query[..., :1])], -1)[:, :, None]
        weights = 1/(relative[..., :2].float().square().sum(-1)+self.radius**2)
        weights = weights/weights.sum(-1, keepdim=True)
        attribute = torch.cat([features[batch_index, indices], relative,
                               scales[batch_index, indices], rotations[batch_index, indices],
                               opacities[batch_index, indices], semantics[batch_index, indices]], -1)
        values = self.value(attribute.to(features.dtype))
        pooled = (values*weights[..., None].to(values.dtype)).sum(-2)
        residual = self.readout(torch.cat([pooled, base.to(pooled.dtype),
                                          occupancy[..., None].to(pooled.dtype)], -1)).float()
        # No detach: old Gaussian branch and shared encoder retain semantic gradients.
        return (base.log()+residual).softmax(-1)

    def forward(self, query_xy, means, scales, rotations, opacities, semantics,
                instance_feature, probabilities, occupancy):
        if instance_feature.shape != (*means.shape[:2], self.feature_dim):
            raise ValueError('final_feature_gaussian_alignment_required')
        if probabilities.shape != (*query_xy.shape[:2], self.num_classes):
            raise ValueError('query_probability_shape_required')
        if semantics.shape != (*means.shape[:2], self.num_classes):
            raise ValueError('gaussian_semantic_shape_required')
        if opacities.ndim == 2:
            opacities = opacities[..., None]
        values = (query_xy, means, scales, rotations, opacities, semantics, instance_feature, probabilities, occupancy)
        finite = torch.stack([torch.isfinite(x).all() for x in values]).all()
        if not bool(finite.item()) or bool((probabilities < 0).any().item()):
            raise FloatingPointError('invalid_residual_input')
        base = self.base_probabilities(probabilities)
        if self.training and torch.is_grad_enabled():
            if instance_feature.requires_grad:instance_feature.register_hook(lambda g:self._record_gradient('final_encoder_feature',g))
            if probabilities.requires_grad:probabilities.register_hook(lambda g:self._record_gradient('original_probability_path',g))
        pieces = []
        for start in range(0, query_xy.shape[1], self.query_chunk):
            end = start+self.query_chunk
            arguments = (query_xy[:, start:end], means, scales, rotations, opacities, semantics,
                         instance_feature, base[:, start:end], occupancy[:, start:end])
            if self.training and self.checkpoint_chunks and torch.is_grad_enabled():
                value = checkpoint(self._chunk, *arguments, use_reentrant=False)
            else:
                value = self._chunk(*arguments)
            pieces.append(value)
        nonempty = torch.cat(pieces, 1)
        if not self.last_diagnostics:
            with torch.no_grad():
                old_empty=(1-occupancy)>=occupancy*probabilities[...,:-1].amax(-1)
                new_empty=(1-occupancy)>=occupancy*nonempty.amax(-1)
                positive=probabilities[...,:-1].sum(-1)>0
                error=(nonempty-probabilities[...,:-1]).abs().amax(-1)
                stats=torch.stack([(~positive).sum().float(),
                    ((old_empty!=new_empty)&positive).sum().float(),(nonempty-base).abs().amax(),
                    torch.where(positive,error,torch.zeros_like(error)).amax(),
                    ((old_empty!=new_empty)&~positive).sum().float()]).cpu().tolist()
                self.last_diagnostics=dict(zip(('initial_zero_mass_queries','initial_support_flips',
                    'initial_residual_probability_max_error','initial_positive_output_max_error','initial_zero_mass_support_flips'),stats))
                if stats[1]!=0 or stats[3]>1e-6:
                    raise ValueError('zero_residual_positive_mass_parity_failed')
        return torch.cat([nonempty, torch.zeros_like(nonempty[..., :1])], -1)
