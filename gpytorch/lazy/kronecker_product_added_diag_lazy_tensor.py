#!/usr/bin/env python3

import torch

from .added_diag_lazy_tensor import AddedDiagLazyTensor
from .diag_lazy_tensor import ConstantDiagLazyTensor, DiagLazyTensor
from .matmul_lazy_tensor import MatmulLazyTensor


class KroneckerProductAddedDiagLazyTensor(AddedDiagLazyTensor):
    def __init__(self, *lazy_tensors, preconditioner_override=None):
        super().__init__(*lazy_tensors, preconditioner_override=preconditioner_override)
        if len(lazy_tensors) > 2:
            raise RuntimeError("An AddedDiagLazyTensor can only have two components")
        elif isinstance(lazy_tensors[0], DiagLazyTensor):
            self.diag_tensor = lazy_tensors[0]
            self.lazy_tensor = lazy_tensors[1]
        elif isinstance(lazy_tensors[1], DiagLazyTensor):
            self.diag_tensor = lazy_tensors[1]
            self.lazy_tensor = lazy_tensors[0]
        else:
            raise RuntimeError("One of the LazyTensors input to AddedDiagLazyTensor must be a DiagLazyTensor!")
        self._diag_is_constant = isinstance(self.diag_tensor, ConstantDiagLazyTensor)

    def inv_quad_logdet(self, inv_quad_rhs=None, logdet=False, reduce_inv_quad=True):
        if self._diag_is_constant:
            # we want to call the standard InvQuadLogDet to easily get the probe vectors and do the
            # solve but we only want to cache the probe vectors for the backwards
            inv_quad_term, _ = super().inv_quad_logdet(
                inv_quad_rhs=inv_quad_rhs, logdet=False, reduce_inv_quad=reduce_inv_quad
            )
            logdet_term = self._logdet() if logdet else None
            return inv_quad_term, logdet_term
        return super().inv_quad_logdet(inv_quad_rhs=inv_quad_rhs, logdet=logdet, reduce_inv_quad=reduce_inv_quad)

    def _logdet(self):
        if self._diag_is_constant:
            # symeig requires computing the eigenvectors so that it's differentiable
            evals, _ = self.lazy_tensor.symeig(eigenvectors=True)
            evals_plus_diag = evals + self.diag_tensor.diag()
            return torch.log(evals_plus_diag).sum(dim=-1)
        return super()._logdet()

    def _preconditioner(self):
        # solves don't use CG so don't waste time computing it
        return None, None, None

    def _solve(self, rhs, preconditioner=None, num_tridiag=0):
        if self._diag_is_constant:
            # we can perform the solve using the Kronecker-structured eigendecomposition

            # we do the solve in double for numerical stability issues
            # TODO: Use fp64 registry once #1213 is addressed
            rhs_dtype = rhs.dtype
            rhs = rhs.double()

            evals, q_matrix = self.lazy_tensor.symeig(eigenvectors=True)
            evals, q_matrix = evals.double(), q_matrix.double()

            evals_plus_diagonal = evals + self.diag_tensor.diag()
            evals_root = evals_plus_diagonal.pow(0.5)
            inv_mat_sqrt = DiagLazyTensor(evals_root.reciprocal())

            res = q_matrix.transpose(-2, -1).matmul(rhs)
            res2 = inv_mat_sqrt.matmul(res)

            lazy_lhs = q_matrix.matmul(inv_mat_sqrt)
            return lazy_lhs.matmul(res2).type(rhs_dtype)

        # if isinstance(self.diag_tensor, KroneckerDiagLazyTensor):
        #     # If the diagonal has the same Kronecker structure as the full matrix, we can perform the
        #     # solve by using Woodbury's matrix identity
        #     raise NotImplementedError

        # in other cases we have to fall back to the default
        super()._solve(rhs, preconditioner=preconditioner, num_tridiag=num_tridiag)

    def _root_decomposition(self):
        if self._diag_is_constant:
            evals, q_matrix = self.lazy_tensor.symeig(eigenvectors=True)
            updated_evals = DiagLazyTensor((evals + self.diag_tensor.diag()).pow(0.5))
            return MatmulLazyTensor(q_matrix, updated_evals)
        super()._root_decomposition()

    def _root_inv_decomposition(self, initial_vectors=None):
        if self._diag_is_constant:
            evals, q_matrix = self.lazy_tensor.symeig(eigenvectors=True)
            inv_sqrt_evals = DiagLazyTensor((evals + self.diag_tensor.diag()).pow(-0.5))
            return MatmulLazyTensor(q_matrix, inv_sqrt_evals)
        super()._root_inv_decomposition(initial_vectors=initial_vectors)
