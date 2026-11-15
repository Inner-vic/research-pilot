"""Apply a reviewed mesh-head overlay in a private source copy, never upstream."""
import hashlib
import pathlib
import shutil


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('semantic_overlay_source_contract_changed:'+old[:70])
    return text.replace(old, new, 1)


def apply(source):
    source = pathlib.Path(source)
    here = pathlib.Path(__file__).resolve().parent
    folder = source/'model/head/gaussian_head'
    head_path, aggregate_path = folder/'gaussian_head.py', folder/'continuous_surface_aggregate.py'
    before = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest() for p in (head_path, aggregate_path)}
    text = head_path.read_text(encoding='utf-8')
    text = replace_once(text, 'from .mesh_transformer_aggregate import MeshTransformerSurfaceAggregator',
        'from .mesh_transformer_aggregate import MeshTransformerSurfaceAggregator\nfrom .feature_semantic_residual import FeatureSemanticResidual')
    text = replace_once(text, '        mesh_xy_size=None,\n        **kwargs,',
        '        mesh_xy_size=None,\n        feature_semantic_residual=None,\n        **kwargs,')
    text = replace_once(text, '        self.H = H',
        '        self.feature_semantic_residual = (FeatureSemanticResidual(**feature_semantic_residual) if feature_semantic_residual else None)\n        self.H = H')
    text = replace_once(text, 'def _forward_continuous(self, representation, metas, apply_loss_layers):',
        'def _forward_continuous(self, representation, metas, apply_loss_layers, instance_feature=None):')
    text = replace_once(text, "            aggregated = self.continuous_aggregator(**aggregate_kwargs)",
        "            callback = None\n"
        "            if self.feature_semantic_residual is not None and index == apply_loss_layers[-1]:\n"
        "                if index != len(representation)-1:\n"
        "                    raise ValueError('residual_requires_final_decoder_supervision')\n"
        "                if self.surface_aggregator != 'conditional' or self.continuous_aggregator.neighbor_backend != 'gaussian_first':\n"
        "                    raise ValueError('residual_requires_gaussian_first_conditional_head')\n"
        "                if instance_feature is None:\n"
        "                    raise ValueError('final_instance_feature_required')\n"
        "                def callback(q, m, o, sem, probability, occupancy):\n"
        "                    return self.feature_semantic_residual(q, m, scales, rotations, o, sem, instance_feature, probability, occupancy)\n"
        "            self.continuous_aggregator._semantic_residual_callback = callback\n"
        "            try:\n"
        "                aggregated = self.continuous_aggregator(**aggregate_kwargs)\n"
        "            finally:\n"
        "                self.continuous_aggregator._semantic_residual_callback = None")
    text = replace_once(text, '                representation, metas, apply_loss_layers\n',
        "                representation, metas, apply_loss_layers, kwargs.get('instance_feature')\n")
    compile(text, str(head_path), 'exec')
    head_path.write_text(text, encoding='utf-8')
    text = aggregate_path.read_text(encoding='utf-8')
    text = replace_once(text, '        predicted_empty = self.predicted_empty(occupancy, semantic_flat)',
        "        callback = getattr(self, '_semantic_residual_callback', None)\n"
        "        if callback is not None:\n"
        "            semantic_flat = callback(query_xy, means, opacities, semantic_probability,\n"
        "                semantic_flat.reshape(batch, num_queries, num_classes), occupancy.reshape(batch, num_queries)).reshape(total_queries, num_classes)\n"
        "        predicted_empty = self.predicted_empty(occupancy, semantic_flat)")
    compile(text, str(aggregate_path), 'exec')
    aggregate_path.write_text(text, encoding='utf-8')
    shutil.copyfile(here/'feature_semantic_residual.py', folder/'feature_semantic_residual.py')
    return {'kind':'final-feature-semantic-residual/v1','upstream':before,
        'patched':{str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in (head_path, aggregate_path, folder/'feature_semantic_residual.py')},
        'scope':'private mesh-head copy; support gating occurs after new probabilities, including NLL/cluster statistics'}
