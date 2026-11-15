"""Batch MixUp with independent randomness; tensor backends are supplied remotely."""
import math


class BatchMixup:
    def __init__(self, alpha, seed, torch, numpy):
        if not math.isfinite(alpha) or not 0 < alpha <= 1:
            raise ValueError('mixup_alpha_must_be_in_0_1')
        self.alpha = alpha
        self.torch = torch
        self.beta_rng = numpy.random.default_rng(seed)
        self.permutation_rng = torch.Generator(device='cpu').manual_seed(seed)
        self.batches = 0

    def batch(self, images, targets):
        weight = float(self.beta_rng.beta(self.alpha, self.alpha))
        indices = self.torch.randperm(len(targets), generator=self.permutation_rng,
                                      device='cpu').to(targets.device)
        self.batches += 1
        return weight * images + (1 - weight) * images[indices], targets[indices], weight

    @staticmethod
    def loss(criterion, logits, targets, paired_targets, weight):
        return weight * criterion(logits, targets) + (1 - weight) * criterion(logits, paired_targets)

    def state(self):
        return {'alpha': self.alpha, 'batches': self.batches,
                'beta_rng': self.beta_rng.bit_generator.state,
                'permutation_rng': self.permutation_rng.get_state()}


def remote_controls(torch, numpy):
    """Numerical controls: call only in the authorized development space."""
    images = torch.arange(24, dtype=torch.float32).reshape(4, 2, 3)
    targets = torch.arange(4)
    logits = torch.tensor([[1., 2., 3., 4.], [4., 1., 3., 2.],
                           [2., 3., 1., 4.], [3., 4., 2., 1.]])
    before_torch = torch.get_rng_state().clone()
    before_numpy = numpy.random.get_state()
    first = BatchMixup(.2, 1927, torch, numpy)
    second = BatchMixup(.2, 1927, torch, numpy)
    criterion = torch.nn.CrossEntropyLoss()
    for _ in range(16):
        mixed, paired, weight = first.batch(images, targets)
        duplicate, repeated, other_weight = second.batch(images, targets)
        assert 0 <= weight <= 1 and sorted(paired.tolist()) == targets.tolist()
        assert torch.equal(mixed, duplicate) and torch.equal(paired, repeated) and weight == other_weight
        assert torch.allclose(mixed, weight * images + (1 - weight) * images[paired])
        soft = weight * torch.nn.functional.one_hot(targets, 4) + (1 - weight) * torch.nn.functional.one_hot(paired, 4)
        expected = -(soft * torch.log_softmax(logits, dim=1)).sum(dim=1).mean()
        assert torch.allclose(first.loss(criterion, logits, targets, paired, weight), expected)
    after_numpy = numpy.random.get_state()
    assert torch.equal(before_torch, torch.get_rng_state())
    assert before_numpy[0] == after_numpy[0] and numpy.array_equal(before_numpy[1], after_numpy[1])
    assert before_numpy[2:] == after_numpy[2:]
    return {'paired_inputs_and_targets': True, 'weighted_ce_matches_soft_targets': True,
            'reproducible_independent_rng': True, 'global_torch_numpy_rng_unchanged': True,
            'application': 'one Beta(.2,.2) lambda per minibatch, every minibatch',
            'checked_batches': first.batches, 'device': 'cpu', 'gpu_compute_invoked': False}
