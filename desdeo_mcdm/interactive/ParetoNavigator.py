from desdeo_problem.Problem import MOProblem
from desdeo_tools.scalarization.ASF import PointMethodASF, ReferencePointASF
from desdeo_tools.scalarization.Scalarizer import Scalarizer
from desdeo_tools.solver.ScalarSolver import ScalarMethod, ScalarMinimizer
from desdeo_mcdm.interactive.ReferencePointMethod import validate_reference_point
from typing import Callable, Dict, List, Optional, Tuple, Union
import numpy as np
import pandas as pd
from desdeo_mcdm.interactive.InteractiveMethod import InteractiveMethod
from desdeo_tools.interaction.request import BaseRequest, SimplePlotRequest
from scipy.spatial import ConvexHull

from scipy.optimize import linprog, differential_evolution

#TODO
# Scipy linprog failing :/
# Request: Step back, speed messages
# Request validations
# Request handling documentation in ParetoNavigator
# Initial preference as ref point
# Classification to ref point
# final solution projection to actual po set
# constraints
# Discrete case
# Plot requests
# A lot of checking and validation
# scalar method in init
class ParetoNavigatorException(Exception):
    """Raised when an exception related to Pareto Navigator is encountered.
    """

    pass


# TODO preferred solution preference
class ParetoNavigatorInitialRequest(BaseRequest):
    """
    A request class to handle the Decision Maker's initial preferences for the first iteration round.

    In what follows, the DM is involved. First, the DM is asked to select a starting
    point for the navigation phase.
    """

    def __init__(self, ideal: np.ndarray, nadir: np.ndarray) -> None:
        """
        Initialize with ideal and nadir vectors.

        Args:
            ideal (np.ndarray): Ideal vector. Only needed if using ref point
            nadir (np.ndarray): Nadir vector.Only needed if using ref point
        """

        self._ideal = ideal
        self._nadir = nadir

        msg = "Please specify a starting point as 'preferred_solution'."
        "Or specify a reference point as 'reference_point'."

        content = {
            "message": msg,
            "ideal": ideal,
            "nadir": nadir,
        }

        # Could also be a ref point
        super().__init__("preferred_solution_preference", "required", content=content)

    @classmethod
    def init_with_method(cls, method: InteractiveMethod):
        """
        Initialize request with given instance of ReferencePointMethod.

        Args:
            method (ReferencePointMethod): Instance of ReferencePointMethod-class.
        Returns:
            RPMInitialRequest: Initial request.
        """

        return cls(method._ideal, method._nadir)

    @BaseRequest.response.setter
    def response(self, response: Dict) -> None:
        """
        Set the Decision Maker's response information for initial request.

        Args:
            response (Dict): The Decision Maker's response.

        Raises:
            ParetoNavigatorException: In case reference point 
            or preferred solution is missing.
        """

        if "reference_point" in response:
            validate_reference_point(response["reference_point"], self._ideal, self._nadir)
        elif "preferred_solution" in response:
            # Validate
            pass
        else:
            msg = "Please specify either a starting point as 'preferred_solution'."
            "or a reference point as 'reference_point."
            raise ParetoNavigatorException(msg)
            
        self._response = response


class ParetoNavigatorRequest(BaseRequest):
    """
    A request class to handle the Decision Maker's preferences after the first iteration round.
    """

    def __init__(
        self, 
        current_solution: np.ndarray, 
        ideal: np.ndarray, 
        nadir: np.ndarray,
    ) -> None:
        """
        Initialize request with current iterations's solution process information.

        Args:
            current_solution (np.ndarray): Current solution.
            ideal (np.ndarray): Ideal vector.
            nadir (np.ndarray): Nadir vector.
        """

        self._current_solution = current_solution
        self._ideal = ideal
        self._nadir = nadir

        msg = (
            "If you are satisfied with the current solution, please state: "
            "'satisfied' as 'True'. "
            "If you are not satisfied with the current solution "
            "and wish to change the direction, please state: "
            "1. 'new_direction' as 'True'. "
            "2. 'reference_point' a new reference point "
            "If you wish to continue to the same direction, state: "
            "'new_direction' as 'False' "
        )

        content = {"message": msg, "current_solution": current_solution}

        super().__init__("reference_point_preference", "required", content=content)
    
    @classmethod
    def init_with_method(cls, method: InteractiveMethod):
        """
        Initialize request with given instance of ParetoNavigator.

        Args:
            method (ParetoNavigator): Instance of ParetoNavigator-class.
        Returns:
            ParetoNavigatorRequest: Initial request.
        """

        return cls(method._current_solution, method._ideal, method._nadir)

    @BaseRequest.response.setter
    def response(self, response: Dict) -> None:
        """
        Set the Decision Maker's response information for request.

        Args:
            response (Dict): The Decision Maker's response.

        Raises:
            ParetoNavigatorException: In case response is invalid.
        """
        # if ("new_direction" in response and response['new_direction']):
        #     if 'satisfied' in response and not response['satisfied']:
        #         if "reference_point" not in response:
        #             raise ParetoNavigatorException("New reference point information missing. Please specify it as 'reference_point'.")
        #     else:
        #         validate_reference_point(response["reference_point"], self._ideal, self._nadir)

        self._response = response


class ParetoNavigatorStopRequest(BaseRequest):
    """
    A request class to handle termination.
    """

    def __init__(self, final_solution: np.ndarray, objective_values: np.ndarray = None) -> None:
        """
        Initialize termination request with final solution and objective vector.

        Args:
            final_solution (np.ndarray): Solution (decision variables).
            objective_values (np.ndarray): Objective vector.
        """
        msg = "Final solution found."
        # TODO projection to actual PO set
        content = {
            "message": msg,
            "final_solution": final_solution,
            "objective_values": objective_values
        }

        super().__init__("print", "no_interaction", content=content)


class ParetoNavigator(InteractiveMethod):

    def __init__(
        self,
        problem: MOProblem,
        pareto_optimal_solutions: np.ndarray, # Initial pareto optimal solutions
        ideal: np.ndarray,
        nadir: np.ndarray,
        epsilon: float = 1e-6, # No need?
        # scalar_method: Optional[ScalarMethod] = None
    ):
        if not ideal.shape == nadir.shape:
            raise ParetoNavigatorException("The dimensions of the ideal and nadir point do not match.")

        self._problem = problem

        self._ideal = ideal
        self._nadir = nadir
        self._utopian = ideal - epsilon # No need?
        self._n_objectives = self._ideal.shape[0]

        self._weights = self.calculate_weights(self._ideal, self._nadir)
        A, self.b =  self.polyhedral_set_eq(pareto_optimal_solutions)
        self.lppp_A= self.construct_lppp_A(self._weights, A) # Used in (3), Doesn't change

        self._pareto_optimal_solutions = pareto_optimal_solutions

        # initialize method with MOProblem
        # TODO discrete
        self._objectives: Callable = lambda x: self._problem.evaluate(x).objectives
        self._variable_bounds: Union[np.ndarray, None] = problem.get_variable_bounds()
        self._variable_vectors = None
        self._constraints: Optional[Callable] = lambda x: self._problem.evaluate(x).constraints

        self._allowed_speeds = [1, 2, 3, 4, 5] # Some validation, 1 is slowest
        self._current_speed = 1
        self._reference_point = None

        self._current_solution = None
        
        self._direction = None
    
    def start(self):
        return ParetoNavigatorInitialRequest.init_with_method(self)
    
    def iterate(
        self, 
        request: Union[ParetoNavigatorInitialRequest, ParetoNavigatorRequest, ParetoNavigatorStopRequest]
    ) -> Union[ParetoNavigatorRequest, ParetoNavigatorStopRequest]:
        """
        Perform the next logical iteration step based on the given request type.

        Args:
            request (Union[ParetoNavigatorInitialRequest, ParetoNavigatorRequest,ParetoNavigatorStopRequest]):
            A ParetoNavigatorRequest

        Returns:
            Union[RPMRequest, RPMStopRequest]: A new request with content depending on the Decision Maker's
            preferences.
        """
        
        if type(request) is ParetoNavigatorInitialRequest:
            return self.handle_initial_request(request)
        elif type(request) is ParetoNavigatorRequest:
            return self.handle_request(request)
        else:
            # if stop request, do nothing
            return request

    def handle_initial_request(self, request: ParetoNavigatorInitialRequest) -> ParetoNavigatorRequest:
        if "reference_point" in request.response:
            self._reference_point = request.response["reference_point"]
            # set starting point
            starting_point = self._reference_point # TODO, ref point -> starting point
        else: # Preferred po solution
            starting_point = self._pareto_optimal_solutions[request.response["preferred_solution"]]

        self._current_solution = starting_point

        return ParetoNavigatorRequest.init_with_method(self)

    def handle_request(self, request: ParetoNavigatorRequest) -> Union[ParetoNavigatorRequest, ParetoNavigatorStopRequest]:
        
        resp: dict = request.response
        if "satisfied" in resp and resp["satisfied"]:
            final_solution = self.solve_asf(
                self._current_solution,
                self._nadir,
                self._ideal,
            )
            print("Stopping")
            return ParetoNavigatorStopRequest(final_solution)

        # First iteration after initial, make sure preference is given
        if self._direction is None and ('new_direction' not in resp or not resp['new_direction']):
            if 'reference_point' not in resp: # Or other preference
                raise ParetoNavigatorException("One must specify preference information after starting the method")

        if 'speed' in resp:
            self._current_speed = 5 - resp['speed'] / 5
        
        if 'step_back' in resp and resp['step_back']:
            self._current_speed *= -1
        else: self._current_speed = np.abs(self._current_speed)
        
        if 'new_direction' in resp and resp['new_direction']:
            if 'reference_point' not in resp: 
                raise ParetoNavigatorException("New direction needs preference information")

            self._reference_point = resp['reference_point']
            self._direction = self.calculate_direction(self._current_solution, self._reference_point)
    
        # Get the new solution by solving the linear parametric problem
        self._current_solution = self.solve_linear_parametric_problem(
            self._current_solution,
            self._direction,
            self._current_speed,
            self.lppp_A,
            self.b
        )

        return ParetoNavigatorRequest.init_with_method(self)

    def calculate_weights(self, ideal: np.ndarray, nadir: np.ndarray):
        return 1 / (nadir - ideal)

    def polyhedral_set_eq(self, po_solutions: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        convex_hull = ConvexHull(po_solutions)
        A = convex_hull.equations[:,0:-1]
        b = convex_hull.equations[:,-1]
        return A, b
    
    def construct_lppp_A(self, weights, A):
        """
        The matrix A used in the linear parametric programming problem
        """
        k = len(weights)
        diag = np.zeros((k,k))

        np.fill_diagonal(diag, 1)
        weights_inv = np.reshape(np.vectorize(lambda w: -1/w)(weights), (k,1))
        upper_A = np.hstack((weights_inv, diag))

        fill_zeros = np.zeros((len(A), 1))
        filled_A = np.hstack((fill_zeros, A))

        lppp_A = np.concatenate((upper_A, filled_A))
        return lppp_A
    
    def calculate_direction(self, current_solution: np.ndarray, ref_point: np.ndarray):
        return ref_point - current_solution
    
    def classification_to_ref_point(self):
        pass # TODO, most likely done in nimbus
    
    def solve_linear_parametric_problem(
        self,
        current_sol: np.ndarray, # z^c
        direction: np.ndarray, # d
        a: float, # alpha
        A: np.ndarray, # Az < b
        b: np.ndarray,
    ) -> np.ndarray:
        """
        The linear parametric programming problem  (3)
        """
        k = len(current_sol)
        c = np.array([1] + k*[0])

        moved_ref_point = current_sol + (a * direction) # Z^-
        moved_ref_point = np.reshape(moved_ref_point, ((k,1)))
        b_new = np.append(moved_ref_point, b) # b'

        obj_bounds = np.stack((self._ideal, self._nadir))
        bounds = [(None, None)] + [(x,y) for x,y in obj_bounds.T] # sequence of pairs
        sol = linprog(c = c,A_ub = A, b_ub = b_new, bounds=bounds)
        if sol["success"]:
            return sol["x"][1:] # zeta in index 0.
        else:
            print("failed")
            return sol["x"][1:] 
            #raise ParetoNavigatorException("Couldn't calculate new solution")
        
    def solve_asf(self, ref_point, nadir, ideal):
        pass
        # TODO



if __name__ == "__main__":
    from desdeo_problem.Objective import _ScalarObjective
    from desdeo_problem import variable_builder

    # Objectives
    def f1(xs):
        xs = np.atleast_2d(xs)
        return -xs[:, 0] - xs[:, 1] + 5

    def f2(xs):
        xs = np.atleast_2d(xs)
        return (
            (1/5) *
            (
                np.square(xs[:, 0]) -
                10 * xs[:, 0] +
                np.square(xs[:, 1]) -
                4 * xs[:, 1] + 
                11
            )
        )

    def f3(xs):
        xs = np.atleast_2d(xs)
        return (5 - xs[:, 0])*(xs[:, 1] - 11)

    obj1 = _ScalarObjective("obj1", f1)
    obj2 = _ScalarObjective("obj2", f2)
    obj3 = _ScalarObjective("obj3", f3)

    # TODO other constraints

    # variables
    var_names = ["x1", "x2"]  # Make sure that the variable names are meaningful to you.

    initial_values = np.array([0.5, 0.5])
    lower_bounds = [0, 0]
    upper_bounds = [4, 6]
    bounds = np.stack((lower_bounds, upper_bounds))
    variables = variable_builder(var_names, initial_values, lower_bounds, upper_bounds)

    # problem
    problem = MOProblem(objectives=[obj1, obj2, obj3], variables=variables)  # objectives "seperately"


    from desdeo_mcdm.utilities.solvers import payoff_table_method
    ideal, nadir = payoff_table_method(problem)
    problem.ideal = ideal
    problem.nadir = nadir

    po_sols = np.array([
        [-2, 0, -18],
        [-1, 4.6, -25],
        [0, -3.1, -14.25],
        [1.38, 0.62, -35.33],
        [1.73, 1.72, -38.64],
        [2.48, 1.45, -42.41],
        [5.00, 2.20, -55.00],
    ])

    method = ParetoNavigator(problem, po_sols, ideal, nadir)
    
    request = method.start()
    print(request.content)

    request.response = {
        'preferred_solution': 3,
    }

    request = method.iterate(request)
    print(request.content)

    request.response = {
        'reference_point': np.array([ideal[0], ideal[1], nadir[2]]),
        'new_direction': True,
    }

    for i in range(15):
        request = method.iterate(request)
        print(request.content["current_solution"])

        request.response = {
            'satisfied': False,
        }
    
    cur_sol = request.content["current_solution"]

    request.response = {
        'reference_point': np.array([ideal[0], nadir[1], cur_sol[2]]),
        'new_direction': True,
        'satisfied': False,
    }

    for i in range(15):
        request = method.iterate(request)
        print(request.content["current_solution"])

        request.response = {
            'satisfied': False,
        }
    
    request.response = {
        'reference_point': np.array([-0.32, 2.33, -27.85]),
        'new_direction': True,
        'satisfied': False,
    }
    
    for i in range(10):
        request = method.iterate(request)
        print(request.content["current_solution"])

        request.response = {
            'satisfied': False,
        }

    request.response = {
        'satisfied': True,
    }

    request = method.iterate(request)
    print(request.content["final_solution"])
