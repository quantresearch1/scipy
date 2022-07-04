"""Base class for sparse matrice with a .data attribute

    subclasses must provide a _with_data() method that
    creates a new matrix with the same sparsity pattern
    as self but with a different data array

"""

import numpy as np

from ._base import spmatrix, _ufuncs_with_fixed_point_at_zero
from ._sputils import isscalarlike, validateaxis, matrix

__all__ = []


# TODO implement all relevant operations
# use .data.__methods__() instead of /=, *=, etc.
class _data_matrix(spmatrix):
    def __init__(self):
        spmatrix.__init__(self)

    def _get_dtype(self):
        return self.data.dtype

    def _set_dtype(self, newtype):
        self.data.dtype = newtype
    dtype = property(fget=_get_dtype, fset=_set_dtype)

    def _deduped_data(self):
        if hasattr(self, 'sum_duplicates'):
            self.sum_duplicates()
        return self.data

    def __abs__(self):
        return self._with_data(abs(self._deduped_data()))

    def __round__(self, ndigits=0):
        return self._with_data(np.around(self._deduped_data(), decimals=ndigits))

    def _real(self):
        return self._with_data(self.data.real)

    def _imag(self):
        return self._with_data(self.data.imag)

    def __neg__(self):
        if self.dtype.kind == 'b':
            raise NotImplementedError('negating a sparse boolean '
                                      'matrix is not supported')
        return self._with_data(-self.data)

    def __imul__(self, other):  # self *= other
        if isscalarlike(other):
            self.data *= other
            return self
        else:
            return NotImplemented

    def __itruediv__(self, other):  # self /= other
        if isscalarlike(other):
            recip = 1.0 / other
            self.data *= recip
            return self
        else:
            return NotImplemented

    def astype(self, dtype, casting='unsafe', copy=True):
        dtype = np.dtype(dtype)
        if self.dtype != dtype:
            return self._with_data(
                self._deduped_data().astype(dtype, casting=casting, copy=copy),
                copy=copy)
        elif copy:
            return self.copy()
        else:
            return self

    astype.__doc__ = spmatrix.astype.__doc__

    def conj(self, copy=True):
        if np.issubdtype(self.dtype, np.complexfloating):
            return self._with_data(self.data.conj(), copy=copy)
        elif copy:
            return self.copy()
        else:
            return self

    conj.__doc__ = spmatrix.conj.__doc__

    def copy(self):
        return self._with_data(self.data.copy(), copy=True)

    copy.__doc__ = spmatrix.copy.__doc__

    def count_nonzero(self):
        return np.count_nonzero(self._deduped_data())

    count_nonzero.__doc__ = spmatrix.count_nonzero.__doc__

    def power(self, n, dtype=None):
        """
        This function performs element-wise power.

        Parameters
        ----------
        n : n is a scalar

        dtype : If dtype is not specified, the current dtype will be preserved.
        """
        if not isscalarlike(n):
            raise NotImplementedError("input is not scalar")

        data = self._deduped_data()
        if dtype is not None:
            data = data.astype(dtype)
        return self._with_data(data ** n)

    ###########################
    # Multiplication handlers #
    ###########################

    def _mul_scalar(self, other):
        return self._with_data(self.data * other)


# Add the numpy unary ufuncs for which func(0) = 0 to _data_matrix.
for npfunc in _ufuncs_with_fixed_point_at_zero:
    name = npfunc.__name__

    def _create_method(op):
        def method(self):
            result = op(self._deduped_data())
            return self._with_data(result, copy=True)

        method.__doc__ = ("Element-wise %s.\n\n"
                          "See `numpy.%s` for more information." % (name, name))
        method.__name__ = name

        return method

    setattr(_data_matrix, name, _create_method(npfunc))


def _find_missing_index(ind, n):
    for k, a in enumerate(ind):
        if k != a:
            return k

    k += 1
    if k < n:
        return k
    else:
        return -1


class _minmax_mixin:
    """Mixin for min and max methods.

    These are not implemented for dia_matrix, hence the separate class.
    """

    def _min_or_max_axis(self, axis, min_or_max, explicit):
        N = self.shape[axis]
        if N == 0:
            raise ValueError("zero-size array to reduction operation")
        M = self.shape[1 - axis]

        mat = self.tocsc() if axis == 0 else self.tocsr()
        mat.sum_duplicates()

        major_index, value = mat._minor_reduce(min_or_max)
        if not explicit:
            not_full = np.diff(mat.indptr)[major_index] < N
            value[not_full] = min_or_max(value[not_full], 0)

        mask = value != 0
        major_index = np.compress(mask, major_index)
        value = np.compress(mask, value)

        if axis == 0:
            return self._coo_container(
                (value, (np.zeros(len(value)), major_index)),
                dtype=self.dtype, shape=(1, M)
            )
        else:
            return self._coo_container(
                (value, (major_index, np.zeros(len(value)))),
                dtype=self.dtype, shape=(M, 1)
            )

    def _min_or_max(self, axis, out, min_or_max, explicit):
        if out is not None:
            raise ValueError(("Sparse matrices do not support "
                              "an 'out' parameter."))

        validateaxis(axis)

        if axis is None:
            if 0 in self.shape:
                raise ValueError("zero-size array to reduction operation")

            zero = self.dtype.type(0)
            if self.nnz == 0:
                return zero
            m = min_or_max.reduce(self._deduped_data().ravel())
            if self.nnz != np.prod(self.shape) and not explicit:
                m = min_or_max(zero, m)
            return m

        if axis < 0:
            axis += 2

        if (axis == 0) or (axis == 1):
            return self._min_or_max_axis(axis, min_or_max, explicit)
        else:
            raise ValueError("axis out of range")

    def _arg_min_or_max_axis(self, axis, op, compare, explicit):
        if self.shape[axis] == 0:
            raise ValueError("Can't apply the operation along a zero-sized "
                             "dimension.")

        if axis < 0:
            axis += 2

        zero = self.dtype.type(0)

        mat = self.tocsc() if axis == 0 else self.tocsr()
        mat.sum_duplicates()

        ret_size, line_size = mat._swap(mat.shape)
        ret = np.zeros(ret_size, dtype=int)

        nz_lines, = np.nonzero(np.diff(mat.indptr))
        for i in nz_lines:
            p, q = mat.indptr[i:i + 2]
            data = mat.data[p:q]
            indices = mat.indices[p:q]
            am = op(data)
            m = data[am]
            if explicit:
                if q - p > 0:
                    ret[i] = indices[am]
            else:
                if compare(m, zero) or q - p == line_size:
                    ret[i] = indices[am]
                else:
                    zero_ind = _find_missing_index(indices, line_size)
                    if m == zero:
                        ret[i] = min(am, zero_ind)
                    else:
                        ret[i] = zero_ind

        if axis == 1:
            ret = ret.reshape(-1, 1)

        return matrix(ret)

    def _arg_min_or_max(self, axis, out, op, compare, explicit):
        if out is not None:
            raise ValueError("Sparse matrices do not support "
                             "an 'out' parameter.")

        validateaxis(axis)

        if axis is None:
            if 0 in self.shape:
                raise ValueError("Can't apply the operation to "
                                 "an empty matrix.")

            if self.nnz == 0:
                if explicit:
                        raise ValueError("Can't apply the operation to zero matrix when explicit is True.")
                return 0
            else:
                zero = self.dtype.type(0)
                mat = self.tocoo()
                am = op(mat.data)
                if explicit:
                    return am
                m = mat.data[am]

                if compare(m, zero):
                    # cast to Python int to avoid overflow
                    # and RuntimeError
                    return int(mat.row[am])*mat.shape[1] + int(mat.col[am])
                else:
                    size = np.prod(mat.shape)
                    if size == mat.nnz:
                        return am
                    else:
                        ind = mat.row * mat.shape[1] + mat.col
                        zero_ind = _find_missing_index(ind, size)
                        if m == zero:
                            return min(zero_ind, am)
                        else:
                            return zero_ind

        return self._arg_min_or_max_axis(axis, op, compare, explicit)

    def max(self, axis=None, out=None, explicit=False):
        """
        Return the maximum of the matrix or maximum along an axis.
        This takes all elements into account, not just the non-zero ones.

        Parameters
        ----------
        axis : {-2, -1, 0, 1, None} optional
            Axis along which the sum is computed. The default is to
            compute the maximum over all the matrix elements, returning
            a scalar (i.e., `axis` = `None`).

        out : None, optional
            This argument is in the signature *solely* for NumPy
            compatibility reasons. Do not pass in anything except
            for the default value, as this argument is not used.

        explicit : {False, True} optional
            Indicates whether the nonzero entries of the matrix should be
            considered. If a row/column is empty, a zero will be returned
            to indicate it contains no nonzero values.


        Returns
        -------
        amax : coo_matrix or scalar
            Maximum of `a`. If `axis` is None, the result is a scalar value.
            If `axis` is given, the result is a sparse.coo_matrix of dimension
            ``a.ndim - 1``.

        See Also
        --------
        min : The minimum value of a sparse matrix along a given axis.
        numpy.matrix.max : NumPy's implementation of 'max' for matrices

        """
        return self._min_or_max(axis, out, np.maximum, explicit)

    def min(self, axis=None, out=None, *, explicit=False):
        """
        Return the minimum of the matrix or maximum along an axis.
        This takes all elements into account, not just the non-zero ones.

        Parameters
        ----------
        axis : {-2, -1, 0, 1, None} optional
            Axis along which the sum is computed. The default is to
            compute the minimum over all the matrix elements, returning
            a scalar (i.e., `axis` = `None`).

        out : None, optional
            This argument is in the signature *solely* for NumPy
            compatibility reasons. Do not pass in anything except for
            the default value, as this argument is not used.

        explicit : {False, True} optional
            Indicates whether the nonzero entries of the matrix should be
            considered. If a row/column is empty, a zero will be returned
            to indicate it contains no nonzero values.

        Returns
        -------
        amin : coo_matrix or scalar
            Minimum of `a`. If `axis` is None, the result is a scalar value.
            If `axis` is given, the result is a sparse.coo_matrix of dimension
            ``a.ndim - 1``.

        See Also
        --------
        max : The maximum value of a sparse matrix along a given axis.
        numpy.matrix.min : NumPy's implementation of 'min' for matrices

        """
        return self._min_or_max(axis, out, np.minimum, explicit)

    def argmax(self, axis=None, out=None, *, explicit=False):
        """Return indices of maximum elements along an axis.

        Implicit zero elements are also taken into account. If there are
        several maximum values, the index of the first occurrence is returned.

        Parameters
        ----------
        axis : {-2, -1, 0, 1, None}, optional
            Axis along which the argmax is computed. If None (default), index
            of the maximum element in the flatten data is returned.
        out : None, optional
            This argument is in the signature *solely* for NumPy
            compatibility reasons. Do not pass in anything except for
            the default value, as this argument is not used.
        explicit : {False, True} optional
            Indicates whether the nonzero entries of the matrix should be
            considered. If a row/column is empty, a zero will be returned
            to indicate it contains no nonzero values.

        Returns
        -------
        ind : numpy.matrix or int
            Indices of maximum elements. If matrix, its size along `axis` is 1.
        """
        return self._arg_min_or_max(axis, out, np.argmax, np.greater, explicit)

    def argmin(self, axis=None, out=None, *, explicit=False):
        """Return indices of minimum elements along an axis.

        Implicit zero elements are also taken into account. If there are
        several minimum values, the index of the first occurrence is returned.

        Parameters
        ----------
        axis : {-2, -1, 0, 1, None}, optional
            Axis along which the argmin is computed. If None (default), index
            of the minimum element in the flatten data is returned.
        out : None, optional
            This argument is in the signature *solely* for NumPy
            compatibility reasons. Do not pass in anything except for
            the default value, as this argument is not used.
        explicit : {False, True} optional
            Indicates whether the nonzero entries of the matrix should be
            considered. If a row/column is empty, a zero will be returned
            to indicate it contains no nonzero values.

        Returns
        -------
         ind : numpy.matrix or int
            Indices of minimum elements. If matrix, its size along `axis` is 1.
        """
        return self._arg_min_or_max(axis, out, np.argmin, np.less, explicit)
