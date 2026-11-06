import unittest
from unittest.mock import patch

from hornet.registry.registry_manager import RegistryManager
from hornet.registry.proxy_resolver import ProxyResolver

try:
    import numpy as np
except ImportError:
    np = None


class LinearAlgebraTests(unittest.TestCase):
    def setUp(self):
        self.proxy = ProxyResolver(registry=RegistryManager())

    def call(self, operation, **args):
        return self.proxy.execute('math.linear_algebra.compute', {'operation': operation, **args})

    def value(self, operation, **args):
        result = self.call(operation, **args)
        self.assertEqual(result['status'], 'SUCCESS', result)
        return result['proxy_response']

    def test_missing_optional_dependency_and_closed_schema(self):
        with patch.dict('sys.modules', {'numpy': None}):
            result = self.call('matrix_transpose', matrix=[[1]])
        self.assertEqual(result['status'], 'ERROR')
        self.assertIn('matrixswarm-hornet[math]', result['proxy_response']['message'])
        for args in ({'matrix': [[1]], 'extra': 1}, {'A': [[1]]}, {'matrix': [[True]]}):
            self.assertEqual(self.call('matrix_transpose', **args)['status'], 'ERROR')

    @unittest.skipIf(np is None, 'NumPy is optional')
    def test_matrix_vector_and_validation(self):
        self.assertEqual(self.value('matrix_addition', matrix_a=[[1, 2]], matrix_b=[[3, 4]])['result'], [[4, 6]])
        self.assertEqual(self.value('matrix_multiplication', A=[[1, 2]], B=[[3], [4]])['result'], [[11]])
        self.assertEqual(self.value('matrix_transpose', matrix=[[1, 2]])['result'], [[1], [2]])
        self.assertEqual(self.value('scalar_multiply', matrix=[[1, -2]], scalar=3)['result'], [[3, -6]])
        self.assertAlmostEqual(self.value('matrix_determinant', matrix=[[1, 2], [3, 4]])['determinant'], -2)
        np.testing.assert_allclose(self.value('matrix_inverse', matrix=[[4, 7], [2, 6]])['inverse'], [[.6, -.7], [-.2, .4]])
        self.assertEqual(self.value('vector_dot_product', v1=[1, 2], v2=[3, 4])['dot_product'], 11)
        self.assertEqual(self.value('vector_cross_product', v1=[1, 0, 0], v2=[0, 1, 0])['cross_product'], [0, 0, 1])
        self.assertEqual(self.value('vector_norm', vector=[3, 4])['norm'], 5)
        np.testing.assert_allclose(self.value('orthogonal_projection', matrix=[[1, 0], [0, 0]], vector=[3, 4])['projection'], [3, 0])
        for operation, args in [('matrix_inverse', {'matrix': [[1, 2], [2, 4]]}), ('matrix_transpose', {'matrix': [[1], [2, 3]]}), ('matrix_multiplication', {'A': [[1, 2]], 'B': [[1, 2]]}), ('matrix_transpose', {'matrix': [[1] * 257]})]:
            self.assertEqual(self.call(operation, **args)['status'], 'ERROR')

    @unittest.skipIf(np is None, 'NumPy is optional')
    def test_spaces_solvers_and_decompositions(self):
        a = [[0, 2], [1, 3]]
        self.assertEqual(self.value('solve_linear_system', A=[[1, 1]], b=[2])['solution_type'], 'infinite')
        self.assertEqual(self.value('solve_linear_system', A=[[1], [1]], b=[1, 2])['solution_type'], 'none')
        self.assertEqual(self.value('matrix_rank', matrix=[[1, 2], [2, 4]])['rank'], 1)
        self.assertEqual(self.value('compute_rank', matrix=[[1, 2], [2, 4]])['column_rank'], 1)
        null = self.value('find_null_space_basis', matrix=[[1, 2], [2, 4]])
        self.assertEqual(null['dimension_of_null_space'], 1)
        np.testing.assert_allclose(np.asarray([[1, 2], [2, 4]]) @ np.asarray(null['null_basis_vectors']).T, 0, atol=1e-10)
        self.assertEqual(self.value('find_column_space_basis', matrix=[[1, 2, 0], [2, 4, 1]])['pivot_columns'], [0, 2])
        self.assertEqual(self.value('gram_schmidt_orthogonalization', basis_vectors=[[1, 1], [1, -1], [2, 2]])['rank'], 2)
        lu = self.value('lu_decomposition', matrix=a)
        np.testing.assert_allclose(np.asarray(lu['P_matrix']) @ a, np.asarray(lu['L_matrix']) @ lu['U_matrix'])
        qr = self.value('qr_decomposition', matrix=a)
        np.testing.assert_allclose(np.asarray(qr['Q_matrix']) @ qr['R_matrix'], a, atol=1e-10)
        svd = self.value('singular_value_decomposition', matrix=a)
        np.testing.assert_allclose(np.asarray(svd['U_matrix']) @ np.diag(svd['Sigma_values']) @ svd['Vt_matrix'], a, atol=1e-10)
        eig = self.value('compute_eigenvalues', matrix=[[0, -1], [1, 0]])['eigenvalues']
        self.assertEqual(sorted(round(x['imag']) for x in eig), [-1, 1])
        np.testing.assert_allclose(self.value('least_squares_solution', A=[[1], [1], [1]], b=[1, 2, 3])['solution'], [2])

    @unittest.skipIf(np is None, 'NumPy is optional')
    def test_latex_and_processing(self):
        parsed = self.value('parse_matrix', text=r'A = \begin{bmatrix}1 & 2 \\ 3 & 4\end{bmatrix}')
        self.assertEqual(parsed['parsed_matrix'], [[1, 2], [3, 4]])
        self.assertEqual(parsed['detected_matrix_name'], 'A')
        data = {'parsed_matrix': [[1, 2], [2, 4]]}
        self.assertEqual(self.value('process_matrix', matrix_data=data)['rank'], 1)
        self.assertEqual(self.value('process_matrix', matrix_data=data, action='validate_rank')['row_rank'], 1)
        self.assertEqual(self.value('process_matrix', matrix_data=data, action='compute_properties')['total_elements'], 4)
        self.assertEqual(self.call('parse_matrix', text=r'\begin{bmatrix}x\end{bmatrix}')['status'], 'ERROR')


if __name__ == '__main__':
    unittest.main()
