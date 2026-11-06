"""Numerical matrix operations for the registry; all results are JSON compatible."""
import inspect
import math
import re

try:
    import numpy as np
except ImportError:
    np = None

def _require_numpy():
    if np is None:
        raise RuntimeError("Install NumPy with the matrixswarm-hornet[math] extra.")

def _array(value, ndim, name):
    _require_numpy()
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty array")
    def check(item):
        if isinstance(item, list):
            return all(check(v) for v in item)
        return isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item)
    if not check(value):
        raise ValueError(f"{name} must contain finite real numbers")
    try:
        arr = np.asarray(value, dtype=float)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"{name} must be rectangular and numerical") from exc
    if arr.ndim != ndim or any(size == 0 for size in arr.shape):
        raise ValueError(f"{name} must be a non-empty {ndim}-dimensional array")
    if arr.size > 10000:
        raise ValueError(f"{name} exceeds the 10000-element limit")
    if ndim == 2 and max(arr.shape) > 256:
        raise ValueError(f"{name} exceeds the 256-element matrix dimension limit")
    return arr

def _matrix(value, name="matrix"):
    return _array(value, 2, name)

def _vector(value, name="vector"):
    return _array(value, 1, name)

def _square(a):
    if a.shape[0] != a.shape[1]:
        raise ValueError("A square matrix is required")
    return a

def _json(value):
    if isinstance(value, np.ndarray):
        return _json(value.tolist())
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, complex):
        return float(value.real) if abs(value.imag) < 1e-12 else {"real": float(value.real), "imag": float(value.imag)}
    if isinstance(value, np.generic):
        return _json(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("The numerical result is not finite")
    return value

def _rref(a):
    result = a.copy()
    scale = max(float(np.max(np.abs(result))), np.finfo(float).tiny)
    tol = np.finfo(float).eps * max(a.shape) * scale
    row = 0
    pivots = []
    for col in range(a.shape[1]):
        if row >= a.shape[0]:
            break
        candidate = row + int(np.argmax(np.abs(result[row:, col])))
        if abs(result[candidate, col]) <= tol:
            continue
        result[[row, candidate]] = result[[candidate, row]]
        result[row] /= result[row, col]
        for i in range(a.shape[0]):
            if i != row:
                result[i] -= result[i, col] * result[row]
        pivots.append(col)
        row += 1
    result[np.abs(result) < np.finfo(float).eps * max(a.shape)] = 0
    return result, pivots

def matrix_addition(matrix_a, matrix_b):
    """Add two equally shaped matrices."""
    a, b = _matrix(matrix_a, "matrix_a"), _matrix(matrix_b, "matrix_b")
    if a.shape != b.shape:
        raise ValueError("Matrix dimensions must match")
    return {"result": a + b, "dimensions": list(a.shape)}

def matrix_multiplication(A, B):
    """Multiply A by B."""
    a, b = _matrix(A, "A"), _matrix(B, "B")
    if a.shape[1] != b.shape[0]:
        raise ValueError("Matrix inner dimensions must match")
    if a.shape[0] * b.shape[1] > 10000:
        raise ValueError("Matrix product exceeds the 10000-element limit")
    result = a @ b
    return {"result": result, "dimensions": list(result.shape)}

def matrix_transpose(matrix):
    """Transpose a matrix."""
    a = _matrix(matrix)
    return {"result": a.T, "original_shape": list(a.shape), "transposed_shape": list(a.T.shape)}

def scalar_multiply(matrix, scalar):
    """Multiply every matrix entry by a finite real scalar."""
    a = _matrix(matrix)
    if isinstance(scalar, bool) or not isinstance(scalar, (int, float)) or not math.isfinite(scalar):
        raise ValueError("scalar must be a finite real number")
    return {"result": a * scalar, "dimensions": list(a.shape)}

def matrix_determinant(matrix):
    """Compute the determinant of a square matrix."""
    a = _square(_matrix(matrix))
    return {"determinant": np.linalg.det(a), "is_invertible": int(np.linalg.matrix_rank(a)) == len(a)}

def matrix_inverse(matrix):
    """Compute a square, numerically nonsingular matrix inverse."""
    a = _square(_matrix(matrix))
    if np.linalg.matrix_rank(a) != len(a):
        raise ValueError("Matrix is singular or numerically rank deficient")
    return {"inverse": np.linalg.inv(a), "determinant": np.linalg.det(a)}

def vector_dot_product(v1, v2):
    """Compute the dot product of equally sized vectors."""
    a, b = _vector(v1, "v1"), _vector(v2, "v2")
    if a.shape != b.shape:
        raise ValueError("Vector dimensions must match")
    return {"dot_product": a @ b, "dimension": len(a)}

def vector_cross_product(v1, v2):
    """Compute the cross product of two three-dimensional vectors."""
    a, b = _vector(v1, "v1"), _vector(v2, "v2")
    if a.shape != (3,) or b.shape != (3,):
        raise ValueError("Cross product requires two 3D vectors")
    return {"cross_product": np.cross(a, b), "dimension": 3}

def vector_norm(vector, p=2):
    """Compute an Lp norm for a real vector and positive p >= 1."""
    a = _vector(vector)
    if isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or p < 1:
        raise ValueError("p must be a finite number >= 1")
    return {"norm": np.linalg.norm(a, ord=p), "p": p}

def orthogonal_projection(matrix, vector):
    """Project vector onto the column space, including rank-deficient matrices."""
    a, b = _matrix(matrix), _vector(vector)
    if a.shape[0] != len(b):
        raise ValueError("Vector length must equal the number of matrix rows")
    coeff, _, rank, _ = np.linalg.lstsq(a, b, rcond=None)
    projection = a @ coeff
    return {"projection": projection, "coefficients": coeff, "rank": int(rank), "residual_norm": np.linalg.norm(b - projection)}

def solve_linear_system(A, b):
    """Solve Ax=b, reporting unique, infinite, or inconsistent solutions."""
    a, vector = _matrix(A, "A"), _vector(b, "b")
    if a.shape[0] != len(vector):
        raise ValueError("b must have one value per row of A")
    x, _, rank, _ = np.linalg.lstsq(a, vector, rcond=None)
    residual = float(np.linalg.norm(a @ x - vector))
    tolerance = np.finfo(float).eps * max(a.shape) * (float(np.linalg.norm(a)) * float(np.linalg.norm(x)) + float(np.linalg.norm(vector))) * 10
    consistent = residual <= max(tolerance, np.finfo(float).tiny)
    return {"solution_exists": consistent, "solution_type": ("unique" if rank == a.shape[1] else "infinite") if consistent else "none",
            "solution": x if consistent else None, "rank": int(rank), "residual_norm": residual}

def find_null_space_basis(matrix):
    """Find an orthonormal basis for the right null space."""
    a = _matrix(matrix)
    _, singular, vh = np.linalg.svd(a, full_matrices=True)
    tol = np.finfo(float).eps * max(a.shape) * (singular[0] if len(singular) else 0)
    rank = int(np.sum(singular > tol))
    return {"null_basis_vectors": vh[rank:, :], "dimension_of_null_space": a.shape[1] - rank}

def find_column_space_basis(matrix):
    """Return independent original columns spanning the column space."""
    a = _matrix(matrix)
    _, pivots = _rref(a)
    return {"basis_vectors": [a[:, col] for col in pivots], "pivot_columns": pivots, "rank": len(pivots)}

def compute_rank(matrix):
    """Compute the numerical row and column rank."""
    a = _matrix(matrix)
    rank = int(np.linalg.matrix_rank(a))
    return {"row_rank": rank, "column_rank": rank, "is_full_row_rank": rank == a.shape[0]}

def gram_schmidt_orthogonalization(basis_vectors):
    """Orthonormalize independent input vectors, omitting dependent vectors."""
    vectors = _matrix(basis_vectors, "basis_vectors")
    basis = []
    tolerance = np.finfo(float).eps * max(vectors.shape) * float(np.linalg.norm(vectors))
    for vector in vectors:
        residual = vector.copy()
        for q in basis:
            residual -= np.dot(q, residual) * q
        norm = np.linalg.norm(residual)
        if norm > tolerance:
            basis.append(residual / norm)
    return {"orthonormal_basis": basis, "rank": len(basis), "input_count": len(vectors)}

def qr_decomposition(matrix):
    """Compute reduced QR with A = Q @ R."""
    a = _matrix(matrix)
    q, r = np.linalg.qr(a, mode="reduced")
    return {"Q_matrix": q, "R_matrix": r}

def lu_decomposition(matrix):
    """Compute pivoted LU for a square matrix: P @ A = L @ U."""
    a = _square(_matrix(matrix))
    n = len(a)
    u, lower, perm = a.copy(), np.eye(n), np.eye(n)
    tol = np.finfo(float).eps * n * max(float(np.max(np.abs(a))), np.finfo(float).tiny)
    for j in range(n):
        pivot = j + int(np.argmax(np.abs(u[j:, j])))
        if abs(u[pivot, j]) <= tol:
            continue
        if pivot != j:
            u[[j, pivot]] = u[[pivot, j]]
            perm[[j, pivot]] = perm[[pivot, j]]
            lower[[j, pivot], :j] = lower[[pivot, j], :j]
        for i in range(j + 1, n):
            lower[i, j] = u[i, j] / u[j, j]
            u[i, j:] -= lower[i, j] * u[j, j:]
            u[i, j] = 0
    return {"L_matrix": lower, "U_matrix": u, "P_matrix": perm, "equation": "P @ A = L @ U"}

def compute_eigenvalues(matrix):
    """Compute eigenvalues of a square matrix; complex values have real and imag fields."""
    a = _square(_matrix(matrix))
    return {"eigenvalues": np.linalg.eigvals(a)}

def singular_value_decomposition(matrix):
    """Compute reduced SVD: A = U @ diag(S) @ Vt."""
    a = _matrix(matrix)
    u, singular, vt = np.linalg.svd(a, full_matrices=False)
    return {"U_matrix": u, "Sigma_values": singular, "Vt_matrix": vt, "rank": int(np.linalg.matrix_rank(a))}

def least_squares_solution(A, b):
    """Compute the least-squares solution and residual norm."""
    a, vector = _matrix(A, "A"), _vector(b, "b")
    if a.shape[0] != len(vector):
        raise ValueError("b must have one value per row of A")
    solution, _, rank, singular = np.linalg.lstsq(a, vector, rcond=None)
    return {"solution": solution, "residual_norm": np.linalg.norm(a @ solution - vector), "rank": int(rank), "singular_values": singular}

def matrix_rank(matrix):
    """Compute numerical rank and nullity."""
    a = _matrix(matrix)
    rank = int(np.linalg.matrix_rank(a))
    return {"rank": rank, "nullity": a.shape[1] - rank, "singular_values": np.linalg.svd(a, compute_uv=False)}

def parse_matrix(text):
    r"""Parse numeric LaTeX bmatrix or pmatrix notation."""
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    match = re.search(r"\\begin\{(bmatrix|pmatrix)\}(.*?)\\end\{\1\}", text, re.DOTALL)
    if not match:
        raise ValueError("No bmatrix or pmatrix found")
    rows = re.split(r"\\\\", match.group(2).strip())
    matrix = []
    for row in rows:
        pieces = [part.strip() for part in row.strip().split("&")]
        if not all(pieces):
            raise ValueError("Matrix entries must be numeric and non-empty")
        try:
            matrix.append([float(piece) for piece in pieces])
        except ValueError as exc:
            raise ValueError("Only numeric LaTeX entries are supported") from exc
    a = _matrix(matrix)
    name = re.search(r"([A-Za-z][A-Za-z0-9_]*)\s*=\s*$", text[:match.start()])
    return {"parsed_matrix": a, "detected_matrix_name": name.group(1) if name else None,
            "dimensions": list(a.shape), "is_valid": True}

def process_matrix(matrix_data, action="construct_c"):
    """Process parsed matrix data: construct_c, validate_rank, or compute_properties."""
    if not isinstance(matrix_data, dict):
        raise ValueError("matrix_data must be an object")
    a = _matrix(matrix_data.get("parsed_matrix"), "parsed_matrix")
    operation = action
    if operation == "construct_c":
        rows = a[np.any(a != 0, axis=1)]
        rank = int(np.linalg.matrix_rank(rows)) if len(rows) else 0
        return {"operation": operation, "matrix_c": rows, "rank": rank, "is_full_row_rank": rank == len(rows)}
    if operation == "validate_rank":
        rank = int(np.linalg.matrix_rank(a))
        return {"operation": operation, "rows": a.shape[0], "columns": a.shape[1], "row_rank": rank, "column_rank": rank, "is_full_row_rank": rank == a.shape[0]}
    if operation == "compute_properties":
        nonzero = int(np.count_nonzero(a))
        return {"operation": operation, "rows": a.shape[0], "columns": a.shape[1], "total_elements": int(a.size), "nonzero_elements": nonzero, "zero_elements": int(a.size) - nonzero, "density": nonzero / a.size, "is_square": a.shape[0] == a.shape[1]}
    raise ValueError("Unknown operation; use construct_c, validate_rank, or compute_properties")


OPERATIONS = {name: func for name, func in list(globals().items()) if callable(func) and name in (
    "matrix_addition", "matrix_multiplication", "matrix_transpose", "scalar_multiply", "matrix_determinant", "matrix_inverse",
    "vector_dot_product", "vector_cross_product", "vector_norm", "orthogonal_projection", "solve_linear_system", "find_null_space_basis",
    "find_column_space_basis", "compute_rank", "gram_schmidt_orthogonalization", "qr_decomposition", "lu_decomposition",
    "compute_eigenvalues", "singular_value_decomposition", "least_squares_solution", "matrix_rank", "parse_matrix", "process_matrix")}


def execute(arguments):
    """Run a declared operation without mutating the caller's arguments."""
    try:
        _require_numpy()
        params = dict(arguments)
        operation = params.pop("operation")
        function = OPERATIONS[operation]
        inspect.signature(function).bind(**params)
        return {"status": "SUCCESS", **_json(function(**params))}
    except Exception as exc:
        return {"status": "ERROR", "message": str(exc)}
