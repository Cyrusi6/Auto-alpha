"""StackVM-aware action masks for formula token sampling."""

from __future__ import annotations

import random

import torch

from model_core.ops import operator_arity
from model_core.vm import StackVM
from model_core.vocab import FORMULA_VOCAB


def build_action_mask(prefix_tokens: list[int], max_formula_len: int, min_formula_len: int = 1) -> torch.Tensor:
    mask = torch.zeros(FORMULA_VOCAB.size, dtype=torch.bool)
    if len(prefix_tokens) >= max_formula_len:
        return mask
    depth = _stack_depth(prefix_tokens)
    remaining_after_next = max_formula_len - len(prefix_tokens) - 1
    if not prefix_tokens:
        mask[: FORMULA_VOCAB.feature_count] = True
        return mask
    if depth < 0:
        return mask
    if remaining_after_next <= 0:
        if depth == 1:
            _allow_unary(mask)
        if depth == 2:
            _allow_binary(mask)
        return mask
    if depth >= 0:
        mask[: FORMULA_VOCAB.feature_count] = True
    if depth >= 1:
        _allow_unary(mask)
    if depth >= 2:
        _allow_binary(mask)
    if len(prefix_tokens) + 1 < min_formula_len:
        mask[FORMULA_VOCAB.operator_offset :] = False
    return mask


def masked_sample(logits: torch.Tensor, mask: torch.Tensor, rng: random.Random | None = None) -> int:
    allowed = torch.nonzero(mask.to(dtype=torch.bool), as_tuple=False).flatten().tolist()
    if not allowed:
        raise ValueError("no available actions for current prefix")
    if rng is not None:
        values = torch.softmax(logits.detach().cpu()[allowed], dim=-1).tolist()
        total = sum(values)
        threshold = rng.random() * total
        running = 0.0
        for token, value in zip(allowed, values):
            running += value
            if running >= threshold:
                return int(token)
        return int(allowed[-1])
    masked = logits.detach().clone()
    masked[~mask.to(device=logits.device, dtype=torch.bool)] = -1e9
    return int(torch.multinomial(torch.softmax(masked, dim=-1), 1).item())


def explain_available_actions(prefix_tokens: list[int]) -> list[str]:
    mask = build_action_mask(prefix_tokens, max_formula_len=max(len(prefix_tokens) + 1, 2))
    return [FORMULA_VOCAB.token_name(token) for token in torch.nonzero(mask, as_tuple=False).flatten().tolist()]


def _stack_depth(tokens: list[int]) -> int:
    valid, _reason = StackVM().validate_with_reason(tokens)
    if valid:
        return 1
    depth = 0
    for token in tokens:
        token = int(token)
        if 0 <= token < FORMULA_VOCAB.feature_count:
            depth += 1
        elif FORMULA_VOCAB.operator_offset <= token < FORMULA_VOCAB.size:
            arity = operator_arity(token, FORMULA_VOCAB.operator_offset)
            if depth < arity:
                return -1
            depth = depth - arity + 1
        else:
            return -1
    return depth


def _allow_unary(mask: torch.Tensor) -> None:
    for token in range(FORMULA_VOCAB.operator_offset, FORMULA_VOCAB.size):
        if operator_arity(token, FORMULA_VOCAB.operator_offset) == 1:
            mask[token] = True


def _allow_binary(mask: torch.Tensor) -> None:
    for token in range(FORMULA_VOCAB.operator_offset, FORMULA_VOCAB.size):
        if operator_arity(token, FORMULA_VOCAB.operator_offset) == 2:
            mask[token] = True
