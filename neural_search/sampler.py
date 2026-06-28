"""Neural formula sampler with action-mask constraints."""

from __future__ import annotations

import random

import torch
import torch.nn.functional as F

from factor_store import stable_formula_hash
from model_core.alphagpt import AlphaGPT
from model_core.vm import StackVM
from model_core.vocab import FORMULA_VOCAB

from .action_mask import build_action_mask
from .models import PolicySample


FEATURE_VERSION = "ashare_features_v1"
OPERATOR_VERSION = "ashare_ops_v1"


class NeuralFormulaSampler:
    def __init__(
        self,
        model: AlphaGPT,
        seed: int = 42,
        max_formula_len: int = 8,
        min_formula_len: int = 2,
        max_complexity: int = 24,
        max_lookback: int = 10,
        temperature: float = 1.0,
        top_k_tokens: int | None = None,
    ):
        self.model = model
        self.rng = random.Random(seed)
        self.max_formula_len = int(max_formula_len)
        self.min_formula_len = int(min_formula_len)
        self.max_complexity = int(max_complexity)
        self.max_lookback = int(max_lookback)
        self.temperature = float(temperature)
        self.top_k_tokens = top_k_tokens
        self.vm = StackVM()
        self.torch_generator = torch.Generator(device=next(model.parameters()).device)
        self.torch_generator.manual_seed(int(seed))

    def sample_formula(self, track_grad: bool = False, generation: int = 0) -> PolicySample:
        context = torch.enable_grad() if track_grad else torch.no_grad()
        with context:
            return self._sample(track_grad=track_grad, generation=generation)

    def sample_batch(self, count: int, track_grad: bool = False, generation: int = 0) -> list[PolicySample]:
        return [self.sample_formula(track_grad=track_grad, generation=generation) for _ in range(max(0, count))]

    def _sample(self, track_grad: bool, generation: int) -> PolicySample:
        tokens: list[int] = []
        log_probs = []
        entropies = []
        values = []
        device = next(self.model.parameters()).device
        for _ in range(self.max_formula_len):
            mask = build_action_mask(tokens, self.max_formula_len, self.min_formula_len).to(device)
            if not bool(mask.any()):
                break
            if not tokens:
                allowed = torch.nonzero(mask, as_tuple=False).flatten().tolist()
                token = int(self.rng.choice(allowed))
            else:
                prefix = torch.tensor([tokens], dtype=torch.long, device=device)
                logits, value, _task_probs = self.model(prefix)
                logits = logits[0] / max(self.temperature, 1e-6)
                logits = logits.masked_fill(~mask, -1e9)
                if self.top_k_tokens is not None and self.top_k_tokens > 0:
                    logits = _top_k_filter(logits, self.top_k_tokens)
                probs = F.softmax(logits, dim=-1)
                token_tensor = torch.multinomial(probs, 1, generator=self.torch_generator).reshape(())
                token = int(token_tensor.item())
                distribution = torch.distributions.Categorical(probs=probs)
                log_probs.append(distribution.log_prob(token_tensor))
                entropies.append(distribution.entropy())
                values.append(value.reshape(()))
            tokens.append(token)
            valid, _reason = self.vm.validate_with_reason(tokens)
            if valid and len(tokens) >= self.min_formula_len and len(tokens) < self.max_formula_len and self.rng.random() < 0.35:
                break
        valid, reason = self.vm.validate_with_reason(tokens)
        names = self.vm.canonical_formula(tokens)
        complexity = self.vm.formula_complexity(tokens)
        lookback = self.vm.formula_lookback(tokens)
        if complexity > self.max_complexity:
            valid, reason = False, "complexity_above_limit"
        if lookback > self.max_lookback:
            valid, reason = False, "lookback_above_limit"
        formula_hash = stable_formula_hash(tokens, names, FEATURE_VERSION, OPERATOR_VERSION) if valid else None
        log_prob_tensor = torch.stack(log_probs).sum() if log_probs else torch.tensor(0.0, device=device, requires_grad=track_grad)
        entropy_tensor = torch.stack(entropies).mean() if entropies else torch.tensor(0.0, device=device)
        value_tensor = torch.stack(values).mean() if values else torch.tensor(0.0, device=device, requires_grad=track_grad)
        return PolicySample(
            tokens=tokens,
            names=names,
            log_prob=float(log_prob_tensor.detach().cpu().item()),
            entropy=float(entropy_tensor.detach().cpu().item()),
            valid=bool(valid),
            reason=reason,
            complexity=int(complexity),
            lookback=int(lookback),
            generation=generation,
            formula_hash=formula_hash,
            training_log_prob=log_prob_tensor,
            training_entropy=entropy_tensor,
            training_value=value_tensor,
        )


def _top_k_filter(logits: torch.Tensor, top_k: int) -> torch.Tensor:
    if top_k >= logits.numel():
        return logits
    values, _indices = torch.topk(logits, top_k)
    threshold = values[-1]
    return logits.masked_fill(logits < threshold, -1e9)
