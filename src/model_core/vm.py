"""Stack-based formula executor for A-share factors."""

from __future__ import annotations

import torch

from .ops import OPS_CONFIG, operator_complexity, operator_lookback
from .validity import execute_operator_with_validity
from .vocab import FORMULA_VOCAB, FormulaVocab


class StackVM:
    def __init__(self, vocab: FormulaVocab | None = None):
        self.vocab = vocab or FORMULA_VOCAB
        self.feat_offset = self.vocab.operator_offset
        self.op_map = {idx + self.feat_offset: cfg[1] for idx, cfg in enumerate(OPS_CONFIG)}
        self.arity_map = {idx + self.feat_offset: cfg[2] for idx, cfg in enumerate(OPS_CONFIG)}

    def describe(self, formula_tokens: list[int]) -> list[str]:
        return self.vocab.decode_tokens([int(token) for token in formula_tokens])

    def validate(self, formula_tokens: list[int]) -> bool:
        return self.validate_with_reason(formula_tokens)[0]

    def validate_with_reason(self, formula_tokens: list[int]) -> tuple[bool, str]:
        if not formula_tokens:
            return False, "empty formula"
        depth = 0
        for index, token in enumerate(formula_tokens):
            token = int(token)
            if 0 <= token < self.feat_offset:
                depth += 1
            elif token in self.arity_map:
                arity = self.arity_map[token]
                if depth < arity:
                    return False, f"stack underflow at token {index}: {self.vocab.token_name(token)} requires {arity}"
                depth = depth - arity + 1
            else:
                return False, f"unknown token at position {index}: {token}"
        if depth != 1:
            return False, f"multi output stack: final depth is {depth}"
        return True, "ok"

    def formula_complexity(self, formula_tokens: list[int]) -> int:
        complexity = len(formula_tokens)
        for token in formula_tokens:
            token = int(token)
            if token in self.arity_map:
                complexity += operator_complexity(token, self.feat_offset)
        return int(complexity)

    def formula_lookback(self, formula_tokens: list[int], feature_lookbacks: dict[str, int] | None = None) -> int:
        """Return canonical max raw lag, where the current observation is lag zero."""
        stack: list[int] = []
        feature_lookbacks = feature_lookbacks or {}
        for token in formula_tokens:
            token = int(token)
            if 0 <= token < self.feat_offset:
                stack.append(max(0, int(feature_lookbacks.get(self.vocab.token_name(token), 0))))
                continue
            if token not in self.arity_map or len(stack) < self.arity_map[token]:
                return 0
            arity = self.arity_map[token]
            inputs = stack[-arity:]
            del stack[-arity:]
            operator_name = self.vocab.token_name(token)
            operator_window = int(operator_lookback(token, self.feat_offset))
            if operator_name.startswith(("DELAY", "DELTA")):
                incremental = operator_window
            else:
                incremental = max(0, operator_window - 1)
            stack.append(max(inputs) + incremental)
        return int(stack[0]) if len(stack) == 1 else 0

    def formula_semantics(
        self,
        formula_tokens: list[int],
        feature_semantics: dict[str, object],
    ) -> object:
        from feature_factory.semantics import calculate_formula_semantics

        valid, reason = self.validate_with_reason(formula_tokens)
        if not valid:
            raise ValueError(reason)
        operator_arities = {
            self.vocab.token_name(token): int(arity)
            for token, arity in self.arity_map.items()
        }
        operator_windows = {
            self.vocab.token_name(token): int(operator_lookback(token, self.feat_offset))
            for token in self.arity_map
        }
        return calculate_formula_semantics(
            self.canonical_formula(formula_tokens),
            feature_semantics,
            operator_arities=operator_arities,
            operator_windows=operator_windows,
        )

    def canonical_formula(self, formula_tokens: list[int]) -> list[str]:
        names: list[str] = []
        for token in formula_tokens:
            token = int(token)
            if 0 <= token < self.vocab.size:
                names.append(self.vocab.token_name(token))
            else:
                names.append(f"<unknown:{token}>")
        return names

    def explain_formula(self, formula_tokens: list[int]) -> str:
        valid, reason = self.validate_with_reason(formula_tokens)
        names = self.canonical_formula(formula_tokens) if formula_tokens else []
        return (
            f"formula={' '.join(names) or '<empty>'}; "
            f"valid={valid}; reason={reason}; "
            f"lookback={self.formula_lookback(formula_tokens)}; "
            f"complexity={self.formula_complexity(formula_tokens)}"
        )

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

    def execute_with_validity(
        self,
        formula_tokens: list[int],
        feat_tensor: torch.Tensor,
        feature_validity: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor] | None:
        if feature_validity.shape != feat_tensor.shape:
            raise ValueError("feature validity shape mismatch")
        stack: list[tuple[torch.Tensor, torch.Tensor]] = []
        try:
            for token in formula_tokens:
                token = int(token)
                if 0 <= token < self.feat_offset:
                    if token >= feat_tensor.shape[1]:
                        return None
                    values = feat_tensor[:, token, :]
                    validity = feature_validity[:, token, :].bool() & torch.isfinite(values)
                    stack.append((torch.where(validity, values, torch.zeros_like(values)), validity))
                    continue
                if token not in self.op_map:
                    return None
                arity = self.arity_map[token]
                if len(stack) < arity:
                    return None
                inputs = stack[-arity:]; del stack[-arity:]
                values = [item[0] for item in inputs]
                masks = [item[1] for item in inputs]
                result, valid = execute_operator_with_validity(token, self.feat_offset, values, masks)
                stack.append((result, valid))
        except (RuntimeError, TypeError, ValueError, KeyError):
            return None
        return stack[0] if len(stack) == 1 else None
