"""Stack-based formula executor for A-share factors."""

from __future__ import annotations

import torch

from .ops import OPS_CONFIG
from .vocab import FORMULA_VOCAB


class StackVM:
    def __init__(self):
        self.feat_offset = FORMULA_VOCAB.operator_offset
        self.op_map = {idx + self.feat_offset: cfg[1] for idx, cfg in enumerate(OPS_CONFIG)}
        self.arity_map = {idx + self.feat_offset: cfg[2] for idx, cfg in enumerate(OPS_CONFIG)}

    def describe(self, formula_tokens: list[int]) -> list[str]:
        return FORMULA_VOCAB.decode_tokens([int(token) for token in formula_tokens])

    def validate(self, formula_tokens: list[int]) -> bool:
        depth = 0
        for token in formula_tokens:
            token = int(token)
            if 0 <= token < self.feat_offset:
                depth += 1
            elif token in self.arity_map:
                arity = self.arity_map[token]
                if depth < arity:
                    return False
                depth = depth - arity + 1
            else:
                return False
        return depth == 1

    def execute(self, formula_tokens: list[int], feat_tensor: torch.Tensor) -> torch.Tensor | None:
        stack: list[torch.Tensor] = []
        try:
            for token in formula_tokens:
                token = int(token)
                if 0 <= token < self.feat_offset:
                    if token >= feat_tensor.shape[1]:
                        return None
                    stack.append(feat_tensor[:, token, :])
                    continue

                if token not in self.op_map:
                    return None
                arity = self.arity_map[token]
                if len(stack) < arity:
                    return None
                args = stack[-arity:]
                del stack[-arity:]
                result = self.op_map[token](*args)
                stack.append(torch.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0))
        except (RuntimeError, TypeError, ValueError):
            return None

        if len(stack) != 1:
            return None
        return torch.nan_to_num(stack[0], nan=0.0, posinf=0.0, neginf=0.0)
