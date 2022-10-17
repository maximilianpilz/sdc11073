"""
This Module contains code handles Service Controller operations (sco).
All remote control commands of a client are executed by sco's

These operations share a common behavior:
A remote control command is executed async. The respone to such soap request contains a state (typically 'wait') and a transaction id.
The progress of the transaction is reported with an OperationInvokedReport.
A client must subscribe to the OperationInvokeReport Event of the 'Set' service, otherwise it would not get informed about progress.
"""
import inspect
import queue
import sys
import threading
import time
import traceback
from abc import ABC, abstractmethod

from .. import loghelper
from .. import observableproperties as properties
from ..pmtypes import InvocationState, SafetyClassification
from .. import pm_qnames as pm
from .. import msg_qnames as msg


class OperationDefinition:
    """ This is the base class of all provided operations.
    An operation is a point for remote control over the network."""
    current_value = properties.ObservableProperty(fire_only_on_changed_value=False)
    current_request = properties.ObservableProperty(fire_only_on_changed_value=False)
    current_argument = properties.ObservableProperty(fire_only_on_changed_value=False)
    OP_DESCR_QNAME = None
    OP_STATE_QNAME = None
    OP_QNAME = None

    def __init__(self, handle, operation_target_handle,
                 safety_classification=SafetyClassification.INF,
                 coded_value=None,
                 log_prefix=None):  # pylint:disable=too-many-arguments
        """
        :param handle: the handle of the operation itself.
        :param operation_target_handle: the handle of the modified data (MdDescription)
        :param safety_classification: one of pmtypes.SafetyClassification values
        :param coded_value: a pmtypes.CodedValue instance
        """
        self._logger = loghelper.get_logger_adapter(f'sdc.device.op.{self.__class__.__name__}', log_prefix)
        self._mdib = None
        self._descriptor_container = None
        self._operation_state_container = None
        self._operation_target_container = None
        self._handle = handle
        self._operation_target_handle = operation_target_handle
        # documentation of operation_target_handle:
        # A HANDLE reference this operation is targeted to. In case of a single state this is the HANDLE of the descriptor.
        # In case that multiple states may belong to one descriptor (pm:AbstractMultiState), OperationTarget is the HANDLE
        # of one of the state instances (if the state is modified by the operation).
        self._safety_classification = safety_classification
        self._coded_value = coded_value
        self.calls = []  # record when operation was called

    @property
    def handle(self):
        return self._handle

    @property
    def operation_target_handle(self):
        return self._operation_target_handle

    @property
    def operation_target_storage(self):
        return self._mdib.states

    def execute_operation(self, request, operation_request):  # pylint: disable=unused-argument
        """ This is the code that executes the operation itself.
        A handler that executes the operation must be bound to observable "current_request"."""
        self.calls.append((time.time(), request))
        self.current_request = request
        self.current_argument = operation_request.argument

    def set_mdib(self, mdib, parent_descriptor_container):
        """ The operation needs to know the mdib that it operates on.
        This is called by SubscriptionManager on registration.
        Needs to be implemented by derived classes if specific things have to be initialized."""
        if self._mdib is not None:
            raise RuntimeError('Mdib is already set')
        self._mdib = mdib
        self._logger.log_prefix = mdib.log_prefix  # use same prefix as mdib for logging
        self._descriptor_container = self._mdib.descriptions.handle.get_one(self._handle, allow_none=True)
        if self._descriptor_container is not None:
            # there is already a descriptor
            self._logger.info('descriptor for operation "{}" is already present, re-using it', self._handle)
        else:
            cls = mdib.sdc_definitions.get_descriptor_container_class(self.OP_DESCR_QNAME)
            self._descriptor_container = cls(self._handle, parent_descriptor_container.Handle)
            self._init_operation_descriptor_container()
            mdib.descriptions.add_object(self._descriptor_container)

        self._operation_state_container = self._mdib.states.descriptorHandle.get_one(self._handle, allow_none=True)
        if self._operation_state_container is not None:
            self._logger.info('operation state for operation "{}" is already present, re-using it', self._handle)
        else:
            cls = mdib.sdc_definitions.get_state_container_class(self.OP_STATE_QNAME)
            self._operation_state_container = cls(self._descriptor_container)
            mdib.states.add_object(self._operation_state_container)

        # now add the object that is target of operation
        self._init_operation_target_container()

    def _init_operation_descriptor_container(self):
        self._descriptor_container.OperationTarget = self._operation_target_handle
        if self._coded_value is not None:
            self._descriptor_container.Type = self._coded_value

    def _init_operation_target_container(self):
        """ Create the object that is manipulated by the operation"""
        operation_target_descriptor = self._mdib.descriptions.handle.get_one(self._operation_target_handle)
        self._operation_target_container = self._mdib.states.descriptorHandle.get_one(self._operation_target_handle,
                                                                                      allow_none=True)  # pylint:disable=protected-access
        if self._operation_target_container is not None:
            self._logger.info('operation target state for operation "{}" is already present, re-using it',
                              self._operation_target_handle)
        else:
            self._operation_target_container = self._mdib.mk_state_container_from_descriptor(
                operation_target_descriptor)
            self._logger.info('creating {} DescriptorHandle = {}', self._operation_target_container.__class__.__name__,
                              self._operation_target_handle)
            if self._operation_target_container is not None:
                storage = self._mdib.context_states if self._operation_target_container.isMultiState else self._mdib.states
                storage.add_object(self._operation_target_container)

    def set_operating_mode(self, mode):
        """ Mode is one of En, Dis, NA"""
        with self._mdib.transaction_manager() as mgr:
            state = mgr.get_state(self._handle)
            state.OperatingMode = mode

    def collect_values(self, number_of_values=None):
        """ Async way to retrieve next value(s):
        Returns a Future-like object that has a result() method.
        For details see properties.SingleValueCollector and propertiesValuesCollector documentation.
        """
        if number_of_values is None:
            return properties.SingleValueCollector(self, 'current_value')
        return properties.ValuesCollector(self, 'current_value', number_of_values)

    def __str__(self):
        return f'{self.__class__.__name__} handle={self._handle} operation-target={self._operation_target_handle}'


class _SetStringOperation(OperationDefinition):
    OP_DESCR_QNAME = pm.SetStringOperationDescriptor
    OP_STATE_QNAME = pm.SetStringOperationState
    OP_QNAME = msg.SetString

    def __init__(self, handle, operation_target_handle, initial_value=None, coded_value=None):
        super().__init__(handle=handle,
                         operation_target_handle=operation_target_handle,
                         coded_value=coded_value)
        self.current_value = initial_value

    @classmethod
    def from_operation_container(cls, operation_container):
        return cls(handle=operation_container.handle,
                   operation_target_handle=operation_container.OperationTarget,
                   initial_value=None, coded_value=None)


class _SetValueOperation(OperationDefinition):
    OP_DESCR_QNAME = pm.SetValueOperationDescriptor
    OP_STATE_QNAME = pm.SetValueOperationState
    OP_QNAME = msg.SetValue

    def __init__(self, handle, operation_target_handle, initial_value=None, coded_value=None):
        super().__init__(handle=handle,
                         operation_target_handle=operation_target_handle,
                         coded_value=coded_value)
        self.current_value = initial_value


class _SetContextStateOperation(OperationDefinition):
    """Default implementation of SetContextOperation."""
    OP_DESCR_QNAME = pm.SetContextStateOperationDescriptor
    OP_STATE_QNAME = pm.SetContextStateOperationState
    OP_QNAME = msg.SetContextState

    def __init__(self, handle, operation_target_handle, coded_value=None):
        super().__init__(handle,
                         operation_target_handle,
                         coded_value=coded_value)

    @property
    def operation_target_storage(self):
        return self._mdib.context_states

    def _init_operation_target_container(self):
        """ initially no patient context is created."""

    @classmethod
    def from_operation_container(cls, operation_container):
        return cls(handle=operation_container.handle,
                   operation_target_handle=operation_container.OperationTarget)


class _ActivateOperation(OperationDefinition):
    """ This default implementation only registers calls, no manipulation of operation target
    """
    OP_DESCR_QNAME = pm.ActivateOperationDescriptor
    OP_STATE_QNAME = pm.ActivateOperationState
    OP_QNAME = msg.Activate

    def __init__(self, handle, operation_target_handle, coded_value=None):
        super().__init__(handle=handle,
                         operation_target_handle=operation_target_handle,
                         coded_value=coded_value)


class _SetAlertStateOperation(OperationDefinition):
    """ This default implementation only registers calls, no manipulation of operation target
    """
    OP_DESCR_QNAME = pm.SetAlertStateOperationDescriptor
    OP_STATE_QNAME = pm.SetAlertStateOperationState
    OP_QNAME = msg.SetAlertState

    def __init__(self, handle, operation_target_handle, coded_value=None, log_prefix=None):
        super().__init__(handle=handle,
                         operation_target_handle=operation_target_handle,
                         coded_value=coded_value,
                         log_prefix=log_prefix)


class _SetComponentStateOperation(OperationDefinition):
    """ This default implementation only registers calls, no manipulation of operation target
    """
    OP_DESCR_QNAME = pm.SetComponentStateOperationDescriptor
    OP_STATE_QNAME = pm.SetComponentStateOperationState
    OP_QNAME = msg.SetComponentState

    def __init__(self, handle, operation_target_handle, coded_value=None, log_prefix=None):
        super().__init__(handle=handle,
                         operation_target_handle=operation_target_handle,
                         coded_value=coded_value,
                         log_prefix=log_prefix)


class _SetMetricStateOperation(OperationDefinition):
    """ This default implementation only registers calls, no manipulation of operation target
    """
    OP_DESCR_QNAME = pm.SetMetricStateOperationDescriptor
    OP_STATE_QNAME = pm.SetMetricStateOperationState
    OP_QNAME = msg.SetMetricState

    def __init__(self, handle, operation_target_handle, coded_value=None, log_prefix=None):
        super().__init__(handle=handle,
                         operation_target_handle=operation_target_handle,
                         coded_value=coded_value,
                         log_prefix=log_prefix)


# mapping of states: xsi:type information to classes
# find all classes in this module that have a member "OP_DESCR_QNAME"
_classes = inspect.getmembers(sys.modules[__name__],
                              lambda member: inspect.isclass(member) and member.__module__ == __name__)
_classes_with_QNAME = [c[1] for c in _classes if hasattr(c[1], 'OP_DESCR_QNAME') and c[1].OP_DESCR_QNAME is not None]
# make a dictionary from found classes: (Key is OP_DESCR_QNAME, value is the class itself
_operation_lookup_by_type = {c.OP_DESCR_QNAME: c for c in _classes_with_QNAME}


def get_operation_class(q_name):
    """
    :param q_name: a QName instance
    """
    return _operation_lookup_by_type.get(q_name)


class _OperationsWorker(threading.Thread):
    """ Thread that enqueues and processes all operations.
    It manages transaction ids for all operations.
    Progress notifications are sent via subscription manager."""

    def __init__(self, subscriptions_mgr, mdib, log_prefix):
        """
        :param subscriptions_mgr: subscriptionsmgr.notify_operation is called in order to notify all subscribers of OperationInvokeReport Events
        """
        super().__init__(name='DeviceOperationsWorker')
        self.daemon = True
        self._subscriptions_mgr = subscriptions_mgr
        self._mdib = mdib
        self._operations_queue = queue.Queue(10)  # spooled operations
        self._transaction_id = 1
        self._transaction_id_lock = threading.Lock()
        self._logger = loghelper.get_logger_adapter('sdc.device.op_worker', log_prefix)

    def enqueue_operation(self, operation, request, operation_request):
        """ enqueues operation "operation".
        :param operation: a callable with signature operation(request, mdib)
        :param request: the soapEnvelope of the request
        :param argument: parsed argument for the operation handler
        @return: a transaction Identifier
        """
        with self._transaction_id_lock:
            transaction_id = self._transaction_id
            self._transaction_id += 1
        self._operations_queue.put((transaction_id, operation, request, operation_request), timeout=1)
        return transaction_id

    def run(self):
        while True:
            try:
                from_queue = self._operations_queue.get()
                if from_queue == 'stop_sco':
                    self._logger.info('stop request found. Terminating now.')
                    return
                tr_id, operation, request, operation_request = from_queue  # unpack tuple
                time.sleep(0.001)
                self._logger.info('{}: starting operation "{}" argument={}',
                                  operation.__class__.__name__, operation.handle, operation_request.argument)
                # duplicate the WAIT response to the operation request as notification. Standard requires this.
                self._subscriptions_mgr.notify_operation(
                    operation, tr_id, InvocationState.WAIT,
                    self._mdib.mdib_version, self._mdib.sequence_id, self._mdib.nsmapper)
                time.sleep(0.001)  # not really necessary, but in real world there might also be some delay.
                self._subscriptions_mgr.notify_operation(
                    operation, tr_id, InvocationState.START,
                    self._mdib.mdib_version, self._mdib.sequence_id, self._mdib.nsmapper)
                try:
                    operation.execute_operation(request, operation_request)
                    self._logger.info('{}: successfully finished operation "{}"', operation.__class__.__name__,
                                      operation.handle)
                    self._subscriptions_mgr.notify_operation(
                        operation, tr_id, InvocationState.FINISHED,
                        self._mdib.mdib_version, self._mdib.sequence_id, self._mdib.nsmapper)
                except Exception as ex:
                    self._logger.info('{}: error executing operation "{}": {}', operation.__class__.__name__,
                                      operation.handle, traceback.format_exc())
                    self._subscriptions_mgr.notify_operation(
                        operation, tr_id, InvocationState.FAILED,
                        self._mdib.mdib_version, self._mdib.sequence_id, self._mdib.nsmapper,
                        error='Oth', error_message=repr(ex))
            except Exception:
                self._logger.error('{}: unexpected error while handling operation "{}": {}',
                                   operation.__class__.__name__, operation.handle, traceback.format_exc())

    def stop(self):
        self._operations_queue.put('stop_sco')  # a dummy request to stop the thread
        self.join(timeout=1)


class AbstractScoOperationsRegistry(ABC):
    def __init__(self, subscriptions_mgr, operation_cls_getter, mdib, handle='_sco', log_prefix=None):
        self._worker = None
        self._subscriptions_mgr = subscriptions_mgr
        self.operation_cls_getter = operation_cls_getter
        self._mdib = mdib
        self._log_prefix = log_prefix
        self._logger = loghelper.get_logger_adapter('sdc.device.op_reg', log_prefix)
        self._registered_operations = {}  # lookup by handle
        self._handle = handle

        # find the Sco of the Mds, this will be the default sco for new operations
        mds_descriptor_container = mdib.descriptions.NODETYPE.get_one(pm.MdsDescriptor)
        sco_containers = mdib.descriptions.find(parent_handle=mds_descriptor_container.Handle).find(
            NODETYPE=pm.ScoDescriptor).objects
        if len(sco_containers) == 1:
            self._logger.info('found Sco node in mds, using it')
            self._mds_sco_descriptor_container = sco_containers[0]
        else:
            self._logger.info('not found Sco node in mds, creating it')
            # create sco and add to mdib
            cls = mdib.sdc_definitions.get_descriptor_container_class(pm.ScoDescriptor)
            self._mds_sco_descriptor_container = cls(self._handle, mds_descriptor_container.Handle)
            mdib.descriptions.add_object(self._mds_sco_descriptor_container)

    @abstractmethod
    def register_operation(self, operation: OperationDefinition, sco_descriptor_container=None) -> None:
        """

        :param operation: OperationDefinition
        :param sco_descriptor_container: a descriptor container
        :return:
        """

    @abstractmethod
    def unregister_operation_by_handle(self, operation_handle: str) -> None:
        """

        :param operation_handle:
        :return:
        """

    @abstractmethod
    def get_operation_by_handle(self, operation_handle: str) -> OperationDefinition:
        """

        :param operation_handle:
        :return:
        """

    @abstractmethod
    def enqueue_operation(self, operation: OperationDefinition, request, argument):
        """ enqueues operation "operation".
        :param operation: a callable with signature operation(request, mdib)
        :param request: the soapEnvelope of the request
        @return: a transaction Id
        """

    @abstractmethod
    def start_worker(self):
        """ start worker thread"""

    @abstractmethod
    def stop_worker(self):
        """ stop worker thread"""


class ScoOperationsRegistry(AbstractScoOperationsRegistry):
    """ Registry for Sco operations.
    from BICEPS:
    A service control object to define remote control operations. Any pm:AbstractOperationDescriptor/@OperationTarget
    within this SCO SHALL only reference this or child descriptors within the CONTAINMENT TREE.
    NOTE - In modular systems, dynamically plugged-in modules would typically be modeled as VMDs.
    Such VMDs potentially have their own SCO. In every other case, SCO operations are modeled in pm:MdsDescriptor/pm:Sco.
    """

    def register_operation(self, operation: OperationDefinition, sco_descriptor_container=None):
        self._logger.info('register operation "{}"', operation)
        if operation.handle in self._registered_operations:
            self._logger.info('handle {} is already registered, will re-use it', operation.handle)
        parent_container = sco_descriptor_container or self._mds_sco_descriptor_container
        operation.set_mdib(self._mdib, parent_container)
        self._registered_operations[operation.handle] = operation

    def unregister_operation_by_handle(self, operation_handle: str):
        del self._registered_operations[operation_handle]

    def get_operation_by_handle(self, operation_handle: str) -> OperationDefinition:
        return self._registered_operations.get(operation_handle)

    def enqueue_operation(self, operation: OperationDefinition, request, operation_request):
        """ enqueues operation "operation".
        :param operation: a callable with signature operation(request, mdib)
        :param request: the soapEnvelope of the request
        @return: a transaction Id
        """
        return self._worker.enqueue_operation(operation, request, operation_request)

    def start_worker(self):
        if self._worker is not None:
            raise RuntimeError('SCO worker is already running')
        self._worker = _OperationsWorker(self._subscriptions_mgr, self._mdib, self._log_prefix)
        self._worker.start()

    def stop_worker(self):
        if self._worker is not None:
            self._worker.stop()
            self._worker = None
