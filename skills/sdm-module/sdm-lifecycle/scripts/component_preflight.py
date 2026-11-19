"""Bounded remote engineering checks rendered into the protected trainer.

Importing this module never imports or executes ML. The emitted function runs
only in the authorized component, before full fitting, restoring initial state.
"""


def remote_preflight(model, optimizer, criterion, x, y, policy):
    import copy
    import io
    import random
    import numpy as np
    import torch

    if not isinstance(criterion,torch.nn.Module) or any(p.requires_grad for p in criterion.parameters()):
        raise ValueError('governed preflight requires an nn.Module loss without separately learned parameters')

    def snapshot():
        return copy.deepcopy({'model': model.state_dict(), 'optimizer': optimizer.state_dict(),
            'criterion': criterion.state_dict(), 'torch_rng': torch.get_rng_state(),
            'numpy_rng': np.random.get_state(), 'python_rng': random.getstate(),
            'model_modes':{n:m.training for n,m in model.named_modules()},
            'criterion_modes':{n:m.training for n,m in criterion.named_modules()}})

    def restore(state):
        model.load_state_dict(state['model']); optimizer.load_state_dict(state['optimizer'])
        criterion.load_state_dict(state['criterion']); torch.set_rng_state(state['torch_rng'])
        np.random.set_state(state['numpy_rng']); random.setstate(state['python_rng'])
        for n,m in model.named_modules():m.training=state['model_modes'][n]
        for n,m in criterion.named_modules():m.training=state['criterion_modes'][n]
        optimizer.zero_grad(set_to_none=True)

    def equal(a, b):
        if isinstance(a, torch.Tensor): return isinstance(b, torch.Tensor) and torch.equal(a, b)
        if isinstance(a, np.ndarray): return isinstance(b, np.ndarray) and np.array_equal(a, b)
        if isinstance(a, dict): return isinstance(b, dict) and a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
        if isinstance(a, (tuple, list)): return type(a) is type(b) and len(a) == len(b) and all(equal(i, j) for i, j in zip(a, b))
        return a == b

    initial = snapshot()
    modes = [(module, module.training) for module in model.modules()]
    criterion_modes = [(module, module.training) for module in criterion.modules()]
    checks = {}
    try:
        indices = torch.cat([(y == label).nonzero().flatten()[:policy['examples_per_class']] for label in (0, 1)])
        if len(indices) < 4: raise ValueError('tiny preflight needs both classes and four examples')
        tx, ty = x[indices], y[indices]
        model.train(); criterion.train()

        def update():
            optimizer.zero_grad(set_to_none=True)
            logits = model(tx)
            if tuple(logits.shape) != (len(tx), 2) or not bool(torch.isfinite(logits).all()):
                raise ValueError('preflight tensor/finite output mismatch')
            loss = criterion(logits, ty)
            if loss.ndim != 0 or not bool(torch.isfinite(loss)): raise ValueError('preflight nonfinite scalar loss')
            loss.backward()
            gradients = [p.grad for p in model.parameters() if p.grad is not None]
            if not gradients or not all(bool(torch.isfinite(g).all()) for g in gradients):
                raise ValueError('preflight nonfinite or missing gradients')
            optimizer.step()
            if not all(bool(torch.isfinite(p).all()) for p in model.parameters()):
                raise ValueError('preflight nonfinite parameters')
            return float(loss.detach()), bool((logits.argmax(1) == ty).all())

        start = None; fitted = False; trajectory = []
        for step in range(policy['max_steps']-2):
            loss, fitted = update()
            if start is None: start = loss
            trajectory.append({'step':step+1,'loss':loss,'all_training_examples_correct':fitted})
            if fitted and loss < start: break
        if not fitted or not loss < start:
            import json
            raise ValueError('bounded tiny-fit did not pass; no full fit; SDM_PREFLIGHT_DIAGNOSTIC '+json.dumps({
                'schema':'sdm-remote-preflight-failure/v1','policy':policy,'training_only':True,
                'labels':ty.detach().cpu().tolist(),'updates':trajectory,
                'full_fit_started':False,'quality_score':None},sort_keys=True,allow_nan=False))
        checks.update(tensor=True, finite=True, tiny_fit=True)
        checkpoint = io.BytesIO(); torch.save(snapshot(), checkpoint)
        update(); expected = snapshot()
        checkpoint.seek(0)
        restore(torch.load(checkpoint, map_location='cpu', weights_only=False))
        update()
        if not equal(snapshot(), expected): raise ValueError('checkpoint continuation mismatch; no full fit')
        checks['resume'] = True
        return {'schema': 'sdm-remote-engineering-preflight/v1', 'policy': policy, 'checks': checks,
                'tiny_steps': step + 1, 'total_update_steps':step + 3, 'initial_tiny_loss': start, 'final_tiny_loss': loss,
                'scope': 'Training examples only; bounded tiny-fit and exact same-environment checkpoint continuation.',
                'geometry': 'Not applicable to the univariate classification adapter.',
                'initial_state_restored': True}
    finally:
        restore(initial)
        if not equal(snapshot(),initial):raise ValueError('initial preflight state was not restored')
        for module, mode in modes + criterion_modes: module.training = mode


def render():
    import ast
    from pathlib import Path
    tree = ast.parse(Path(__file__).read_bytes())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'remote_preflight')
    return ast.unparse(function) + '\n'
