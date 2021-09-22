import copy
import urllib
import uuid
from typing import List

from lxml import etree as etree_

from . import httpserver
from .periodicreports import PeriodicReportsHandler, PeriodicReportsNullHandler
from .hostedserviceimpl import SoapMessageHandler
from .waveforms import WaveformSender
from .. import compression
from .. import loghelper
from .. import pmtypes
from .. import pysoap
from ..location import SdcLocation
from ..namespaces import Prefixes, WSA_ANONYMOUS, DocNamespaceHelper, wsdTag, wsxTag, dpwsTag, nsmap


class SdcDevice:
    DEFAULT_CONTEXTSTATES_IN_GETMDIB = True  # defines if get_mdib and getMdStates contain context states or not.

    defaultInstanceIdentifiers = (pmtypes.InstanceIdentifier(root='rootWithNoMeaning', extension_string='System'),)

    def __init__(self, ws_discovery, this_model, this_device, device_mdib_container, my_uuid=None,
                 validate=True, ssl_context=None,
                 max_subscription_duration=7200, log_prefix='', specific_components=None,
                 chunked_messages=False):  # pylint:disable=too-many-arguments
        """

        :param ws_discovery: a WsDiscovers instance
        :param this_model: a pysoap.soapenvelope.DPWSThisModel instance
        :param this_device: a pysoap.soapenvelope.DPWSThisDevice instance
        :param device_mdib_container: a DeviceMdibContainer instance
        :param my_uuid: a uuid instance or None
        :param validate: bool
        :param ssl_context: if not None, this context is used and https url is used. Otherwise http
        :param max_subscription_duration: max. possible duration of a subscription, default is 7200 seconds
        :param log_prefix: a string
        :param specific_components: a SdcDeviceComponents instance
        :param chunked_messages: bool
        """
        # ssl protocol handling itself is delegated to a handler.
        # Specific protocol versions or behaviours are implemented there.
        self._wsdiscovery = ws_discovery
        self.model = this_model
        self.device = this_device
        self._mdib = device_mdib_container
        self._my_uuid = my_uuid or uuid.uuid4()
        self._validate = validate
        self._ssl_context = ssl_context
        self._max_subscription_duration = max_subscription_duration
        self._log_prefix = log_prefix
        self._components = copy.deepcopy(device_mdib_container.sdc_definitions.DefaultSdcDeviceComponents)
        if specific_components is not None:
            # merge specific stuff into _components
            self._components.merge(specific_components)
        self.chunked_messages = chunked_messages

        self._mdib.log_prefix = log_prefix
        self._compression_methods = compression.CompressionHandler.available_encodings[:]
        self._logger = loghelper.get_logger_adapter('sdc.device', log_prefix)
        self._location = None
        self._http_server_thread = None

        if self._ssl_context is not None:
            self._urlschema = 'https'
        else:
            self._urlschema = 'http'

        self.collect_rt_samples_period = 0.1  # in seconds
        self._waveform_sender = None
        self.contextstates_in_getmdib = self.DEFAULT_CONTEXTSTATES_IN_GETMDIB  # can be overridden per instance

        # host dispatcher provides data of the sdc device itself.
        self._host_dispatcher = SoapMessageHandler(None, get_key_method=self._components.msg_dispatch_method)
        self._host_dispatcher.register_post_handler('{}/Get'.format(Prefixes.WXF.namespace), self._on_get_metadata)
        self._host_dispatcher.register_post_handler('{}/Probe'.format(Prefixes.WSD.namespace), self._on_probe_request)
        self._host_dispatcher.register_post_handler(wsdTag('Probe'), self._on_probe_request)

        # dpws host is needed in metadata
        self.dpws_host = pysoap.soapenvelope.DPWSHost(
            endpoint_references_list=[pysoap.soapenvelope.WsaEndpointReferenceType(self.epr)],
            types_list=self._mdib.sdc_definitions.MedicalDeviceTypesFilter)

        self._hosted_service_dispatcher = httpserver.HostedServiceDispatcher(self._mdib.sdc_definitions, self._logger)

        self._hosted_service_dispatcher.register_hosted_service(self._host_dispatcher)

        # these are initialized in _setup_components:
        self.msg_reader = None
        self.msg_factory = None
        self._subscriptions_manager = None
        self._sco_operations_registry = None
        self._service_factory = None
        self.product_roles = None
        self.hosted_services = None
        device_mdib_container.set_sdc_device(self)
        self._periodic_reports_handler = PeriodicReportsNullHandler()
        self._setup_components()
        self.base_urls = []  # will be set after httpserver is started

    def _setup_components(self):
        self.msg_reader = self._components.msg_reader_class(self._logger)

        self.msg_factory = self._components.msg_factory_class(sdc_definitions=self._mdib.sdc_definitions,
                                                              logger=self._logger)

        cls = self._components.subscriptions_manager_class
        self._subscriptions_manager = cls(self._ssl_context,
                                          self._mdib.sdc_definitions,
                                          self._mdib.schema_validators,
                                          self.msg_factory,
                                          self._compression_methods,
                                          self._max_subscription_duration,
                                          log_prefix=self._log_prefix,
                                          chunked_messages=self.chunked_messages)

        cls = self._components.sco_operations_registry_class
        self._sco_operations_registry = cls(self._subscriptions_manager,
                                            self._components.operation_cls_getter,
                                            self._mdib,
                                            handle='_sco',
                                            log_prefix=self._log_prefix)

        services_factory = self._components.services_factory
        self.hosted_services = services_factory(self,
                                                self._components,
                                                self._mdib.sdc_definitions)
        for dpws_service in self.hosted_services.dpws_hosted_services:
            self._hosted_service_dispatcher.register_hosted_service(dpws_service)
        self.product_roles = self._components.role_provider_class(self._log_prefix)
        self.product_roles.init_operations(self._mdib, self._sco_operations_registry)

    @property
    def localization_storage(self):
        if self.hosted_services.localization_service is not None:
            return self.hosted_services.localization_service.localization_storage
        return None

    def _on_get_metadata(self, request_data):  # pylint: disable=unused-argument
        self._logger.info('_on_get_metadata')
        _nsm = self._mdib.nsmapper
        response = pysoap.soapenvelope.Soap12Envelope(_nsm.doc_ns_map)
        reply_address = request_data.envelope.address.mk_reply_address('{}/GetResponse'.format(Prefixes.WXF.namespace))
        reply_address.addr_to = WSA_ANONYMOUS
        reply_address.message_id = uuid.uuid4().urn
        response.add_header_object(reply_address)
        metadata_node = self._mk_metadata_node()
        response.add_body_element(metadata_node)
        response.validate_body(self.mdib.schema_validators.mex_schema)
        self._logger.debug('returned meta data = {}', response.as_xml(pretty=False))
        return response

    def _on_probe_request(self, http_header, request):  # pylint: disable=unused-argument
        _nsm = DocNamespaceHelper()
        response = pysoap.soapenvelope.Soap12Envelope(_nsm.doc_ns_map)
        reply_address = request.address.mk_reply_address('{}/ProbeMatches'.format(Prefixes.WSD.namespace))
        reply_address.addr_to = WSA_ANONYMOUS
        reply_address.message_id = uuid.uuid4().urn
        response.add_header_object(reply_address)
        probe_match_node = etree_.Element(wsdTag('Probematch'),
                                          nsmap=_nsm.doc_ns_map)
        types = etree_.SubElement(probe_match_node, wsdTag('Types'))
        types.text = '{}:Device {}:MedicalDevice'.format(Prefixes.DPWS.prefix, Prefixes.MDPWS.prefix)
        scopes = etree_.SubElement(probe_match_node, wsdTag('Scopes'))
        scopes.text = ''
        xaddrs = etree_.SubElement(probe_match_node, wsdTag('XAddrs'))
        xaddrs.text = ' '.join(self.get_xaddrs())
        response.add_body_element(probe_match_node)
        return response

    def _validate_dpws(self, node):
        if not self.shall_validate:
            return
        try:
            self.mdib.schema_validators.dpws_schema.assertValid(node)
        except etree_.DocumentInvalid as ex:
            tmp_str = etree_.tostring(node, pretty_print=True).decode('utf-8')
            self._logger.error('invalid dpws: {}\ndata = {}', ex, tmp_str)
            raise

    def _mk_metadata_node(self):
        metadata_node = etree_.Element(wsxTag('Metadata'),
                                       nsmap=self._mdib.nsmapper.doc_ns_map)

        # ThisModel
        metadata_section_node = etree_.SubElement(metadata_node,
                                                  wsxTag('MetadataSection'),
                                                  attrib={'Dialect': '{}/ThisModel'.format(nsmap['dpws'])})
        self.model.as_etree_subnode(metadata_section_node)
        self._validate_dpws(metadata_section_node[-1])

        # ThisDevice
        metadata_section_node = etree_.SubElement(metadata_node,
                                                  wsxTag('MetadataSection'),
                                                  attrib={'Dialect': '{}/ThisDevice'.format(nsmap['dpws'])})
        self.device.as_etree_subnode(metadata_section_node)

        self._validate_dpws(metadata_section_node[-1])

        # Relationship
        metadata_section_node = etree_.SubElement(metadata_node,
                                                  wsxTag('MetadataSection'),
                                                  attrib={'Dialect': '{}/Relationship'.format(nsmap['dpws'])})
        relationship_node = etree_.SubElement(metadata_section_node,
                                              dpwsTag('Relationship'),
                                              attrib={'Type': '{}/host'.format(nsmap['dpws'])})

        self.dpws_host.as_etree_subnode(relationship_node)
        self._validate_dpws(relationship_node[-1])

        # add all hosted services:
        for service in self.hosted_services.dpws_hosted_services:
            service.mk_dpws_hosted_instance().as_etree_subnode(relationship_node)
            self._validate_dpws(relationship_node[-1])
        return metadata_node

    def set_location(self, location: SdcLocation,
                     validators: List[pmtypes.InstanceIdentifier] = defaultInstanceIdentifiers,
                     publish_now: bool = True):
        '''
        :param location: a pysdc.location.SdcLocation instance
        :param validators: a list of pmtypes.InstanceIdentifier objects or None; in that case the defaultInstanceIdentifiers member is used
        :param publish_now: if True, the device is published via its wsdiscovery reference.
        '''
        if location == self._location:
            return

        if self._location is not None:
            self._wsdiscovery.clear_service(self.epr)

        self._location = location

        if location is None:
            return

        self._mdib.set_location(location, validators)
        if publish_now:
            self.publish()

    def publish(self):
        """
        publish device on the network (sends HELLO message)
        :return:
        """
        scopes = self._components.scopes_factory(self._mdib)
        x_addrs = self.get_xaddrs()
        self._wsdiscovery.publish_service(self.epr, self._mdib.sdc_definitions.MedicalDeviceTypesFilter, scopes,
                                          x_addrs)

    @property
    def shall_validate(self):
        return self._validate

    @property
    def mdib(self):
        return self._mdib

    @property
    def subscriptions_manager(self):
        return self._subscriptions_manager

    @property
    def sco_operations_registry(self):
        return self._sco_operations_registry

    @property
    def epr(self):
        # End Point Reference, e.g 'urn:uuid:8c26f673-fdbf-4380-b5ad-9e2454a65b6b'
        return str(self._my_uuid.urn)

    @property
    def path_prefix(self):
        # http path prefix of service e.g '8c26f673-fdbf-4380-b5ad-9e2454a65b6b'
        return str(self._my_uuid.hex)

    def register_operation(self, operation):
        self._sco_operations_registry.register_operation(operation)

    def unregister_operation_by_handle(self, operation_handle):
        self._sco_operations_registry.register_operation(operation_handle)

    def get_operation_by_handle(self, operation_handle):
        return self._sco_operations_registry.get_operation_by_handle(operation_handle)

    def enqueue_operation(self, operation, request, argument):
        return self._sco_operations_registry.enqueue_operation(operation, request, argument)

    def start_all(self, start_rtsample_loop=True, periodic_reports_interval=None, shared_http_server=None):
        """

        :param start_rtsample_loop: flag
        :param periodic_reports_interval: if provided, a value in seconds
        :param shared_http_server: id provided, use this http server. Otherwise device creates its own.
        :return:
        """
        if periodic_reports_interval or self._mdib.retrievability_periodic:
            self._logger.info('starting PeriodicReportsHandler')
            self._periodic_reports_handler = PeriodicReportsHandler(self._mdib,
                                                                    self._subscriptions_manager,
                                                                    periodic_reports_interval)
            self._periodic_reports_handler.start()
        else:
            self._logger.info('no PeriodicReportsHandler')
            self._periodic_reports_handler = PeriodicReportsNullHandler()
        self._start_services(shared_http_server)

        if start_rtsample_loop:
            self.start_rt_sample_loop()

    def _start_services(self, shared_http_server=None):
        """ start the services"""
        self._logger.info('starting services, addr = {}', self._wsdiscovery.get_active_addresses())
        self._sco_operations_registry.start_worker()
        if shared_http_server:
            self._http_server_thread = shared_http_server
        else:
            self._http_server_thread = httpserver.DeviceHttpServerThread(
                my_ipaddress='0.0.0.0', ssl_context=self._ssl_context, supported_encodings=self._compression_methods,
                log_prefix=self._log_prefix, chunked_responses=self.chunked_messages)

            # first start http server, the services need to know the ip port number
            self._http_server_thread.start()
            event_is_set = self._http_server_thread.started_evt.wait(timeout=15.0)
            if not event_is_set:
                self._logger.error('Cannot start device, start event of http server not set.')
                raise RuntimeError('Cannot start device, start event of http server not set.')

        host_ips = self._wsdiscovery.get_active_addresses()
        self._http_server_thread.dispatcher.register_dispatcher(self.path_prefix, self._hosted_service_dispatcher)
        if len(host_ips) == 0:
            self._logger.error('Cannot start device, there is no IP address to bind it to.')
            raise RuntimeError('Cannot start device, there is no IP address to bind it to.')

        port = self._http_server_thread.my_port
        if port is None:
            self._logger.error('Cannot start device, could not bind HTTP server to a port.')
            raise RuntimeError('Cannot start device, could not bind HTTP server to a port.')

        self.base_urls = []  # e.g https://192.168.1.5:8888/8c26f673-fdbf-4380-b5ad-9e2454a65b6b; list has one member for each used ip address
        for addr in host_ips:
            self.base_urls.append(
                urllib.parse.SplitResult(self._urlschema, '{}:{}'.format(addr, port), self.path_prefix, query=None,
                                         fragment=None))

        for host_ip in host_ips:
            self._logger.info('serving Services on {}:{}', host_ip, port)
        self._subscriptions_manager.set_base_urls(self.base_urls)

    def stop_all(self, close_all_connections=True, send_subscription_end=True):
        self.stop_realtime_sample_loop()
        if self._periodic_reports_handler:
            self._periodic_reports_handler.stop()
        self._subscriptions_manager.end_all_subscriptions(send_subscription_end)
        self._sco_operations_registry.stop_worker()
        try:
            self._wsdiscovery.clear_service(self.epr)
        except KeyError:
            self._logger.info('epr "{}" not known in self._wsdiscovery'.format(self.epr))
        if self.product_roles is not None:
            self.product_roles.stop()

    def start_rt_sample_loop(self):
        if self._waveform_sender:
            raise RuntimeError(' realtime send loop already started')
        self._waveform_sender = WaveformSender(self._mdib, self._logger, self.collect_rt_samples_period)
        self._waveform_sender.start()

    def stop_realtime_sample_loop(self):
        if self._waveform_sender:
            self._waveform_sender.stop()

    def get_xaddrs(self):
        addresses = self._wsdiscovery.get_active_addresses()  # these own IP addresses are currently used by discovery
        port = self._http_server_thread.my_port
        xaddrs = []
        for addr in addresses:
            xaddrs.append('{}://{}:{}/{}'.format(self._urlschema, addr, port, self.path_prefix))
        return xaddrs

    def send_metric_state_updates(self, mdib_version, states):
        self._logger.debug('sending metric state updates {}', states)
        self._subscriptions_manager.send_episodic_metric_report(states, self._mdib.nsmapper, mdib_version,
                                                                self.mdib.sequence_id)
        self._periodic_reports_handler.store_metric_states(mdib_version, states)

    def send_alert_state_updates(self, mdib_version, states):
        self._logger.debug('sending alert updates {}', states)
        self._subscriptions_manager.send_episodic_alert_report(states, self._mdib.nsmapper, mdib_version,
                                                               self.mdib.sequence_id)
        self._periodic_reports_handler.store_alert_states(mdib_version, states)

    def send_component_state_updates(self, mdib_version, states):
        self._logger.debug('sending component state updates {}', states)
        self._subscriptions_manager.send_episodic_component_state_report(states, self._mdib.nsmapper,
                                                                         mdib_version,
                                                                         self.mdib.sequence_id)
        self._periodic_reports_handler.store_component_states(mdib_version, states)

    def send_context_state_updates(self, mdib_version, states):
        self._logger.debug('sending context updates {}', states)
        self._subscriptions_manager.send_episodic_context_report(states, self._mdib.nsmapper, mdib_version,
                                                                 self.mdib.sequence_id)
        self._periodic_reports_handler.store_context_states(mdib_version, states)

    def send_operational_state_updates(self, mdib_version, states):
        self._logger.debug('sending operational state updates {}', states)
        self._subscriptions_manager.send_episodic_operational_state_report(states, self._mdib.nsmapper,
                                                                           mdib_version,
                                                                           self.mdib.sequence_id)
        self._periodic_reports_handler.store_operational_states(mdib_version, states)

    def send_realtime_samples_state_updates(self, mdib_version, states):
        self._logger.debug('sending real time sample state updates {}', states)
        self._subscriptions_manager.send_realtime_samples_report(states, self._mdib.nsmapper, mdib_version,
                                                                 self.mdib.sequence_id)

    def send_descriptor_updates(self, mdib_version, updated, created, deleted, states):
        self._logger.debug('sending descriptor updates updated={} created={} deleted={}', updated, created, deleted)
        self._subscriptions_manager.send_descriptor_updates(updated, created, deleted, states,
                                                            self._mdib.nsmapper,
                                                            mdib_version,
                                                            self.mdib.sequence_id)

    def set_used_compression(self, *compression_methods):
        del self._compression_methods[:]
        self._compression_methods.extend(compression_methods)
