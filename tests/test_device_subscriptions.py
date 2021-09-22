import os
import time
import unittest

import sdc11073
from sdc11073 import namespaces
from sdc11073 import observableproperties
from sdc11073 import pmtypes
from sdc11073.loghelper import basic_logging_setup
from sdc11073.pysoap.msgfactory import SoapMessageFactory
from sdc11073.sdcdevice import waveforms
from sdc11073.sdcdevice.httpserver import RequestData
from tests import mockstuff

mdibFolder = os.path.dirname(__file__)

ReceivedSoap12Envelope = sdc11073.pysoap.soapenvelope.ReceivedSoap12Envelope
Soap12Envelope = sdc11073.pysoap.soapenvelope.Soap12Envelope

# pylint: disable=protected-access

CLIENT_VALIDATE = True

# data that is used in report
HANDLES = ("0x34F05506", "0x34F05501", "0x34F05500")
SAMPLES = {"0x34F05506": (5.566406, 5.712891, 5.712891, 5.712891, 5.800781),
           "0x34F05501": (0.1, -0.1, 1.0, 2.0, 3.0),
           "0x34F05500": (3.198242, 3.198242, 3.198242, 3.198242, 3.163574, 1.1)}


class DummySoapClient(object):
    roundtrip_time = observableproperties.ObservableProperty()

    def __init__(self):
        self.sentReports = []
        self.netloc = None

    def post_soap_envelope(self, soapEnvelopeRequest, response_factory=None,
                           schema=None):  # pylint: disable=unused-argument
        self.sentReports.append(soapEnvelopeRequest)
        self.roundtrip_time = 0.001  # dummy

    def post_soap_envelope_to(self, path, soapEnvelopeRequest, response_factory=None, schema=None,
                              msg=''):  # pylint: disable=unused-argument
        self.sentReports.append(soapEnvelopeRequest)
        self.roundtrip_time = 0.001  # dummy


class TestDeviceSubscriptions(unittest.TestCase):

    def setUp(self):
        basic_logging_setup()
        here = os.path.dirname(__file__)
        self.mdib = sdc11073.mdib.DeviceMdibContainer.from_mdib_file(os.path.join(mdibFolder, '70041_MDIB_Final.xml'))

        self._model = sdc11073.pysoap.soapenvelope.DPWSThisModel(manufacturer='Chinakracher GmbH',
                                                                 manufacturer_url='www.chinakracher.com',
                                                                 model_name='BummHuba',
                                                                 model_number='1.0',
                                                                 model_url='www.chinakracher.com/bummhuba/model',
                                                                 presentation_url='www.chinakracher.com/bummhuba/presentation')
        self._device = sdc11073.pysoap.soapenvelope.DPWSThisDevice(friendly_name='Big Bang Practice',
                                                                   firmware_version='0.99',
                                                                   serial_number='87kabuuum889')

        self.wsDiscovery = sdc11073.wsdiscovery.WSDiscoveryWhitelist(['127.0.0.1'])
        self.wsDiscovery.start()
        self.sdc_device = sdc11073.sdcdevice.SdcDevice(self.wsDiscovery, self._model, self._device, self.mdib)
        self.sdc_device.start_all(periodic_reports_interval=1.0)
        self._allDevices = (self.sdc_device,)

    def tearDown(self):
        self.wsDiscovery.stop()
        for d in self._allDevices:
            if d:
                d.stop_all()

    def _verify_proper_namespaces(self, report):
        """We want some namespaces declared only once for small report sizes."""
        import re
        xml_string = report.as_xml().decode('utf-8')
        for ns in (namespaces.Prefixes.PM.namespace,
                   namespaces.Prefixes.MSG.namespace,
                   namespaces.Prefixes.EXT.namespace,
                   namespaces.Prefixes.XSI.namespace,):
            occurances = [i.start() for i in re.finditer(ns, xml_string)]
            self.assertLessEqual(len(occurances), 1)

    def test_waveformSubscription(self):
        for sdcDevice in self._allDevices:
            testSubscr = mockstuff.TestDevSubscription(sdcDevice.mdib.sdc_definitions.Actions.Waveform,
                                                       sdcDevice.mdib.schema_validators)
            sdcDevice.subscriptions_manager._subscriptions.add_object(testSubscr)

            tr = waveforms.TriangleGenerator(min_value=0, max_value=10, waveformperiod=2.0, sampleperiod=0.01)
            st = waveforms.SawtoothGenerator(min_value=0, max_value=10, waveformperiod=2.0, sampleperiod=0.01)
            si = waveforms.SinusGenerator(min_value=-8.0, max_value=10.0, waveformperiod=5.0, sampleperiod=0.01)

            sdcDevice.mdib.register_waveform_generator(HANDLES[0], tr)
            sdcDevice.mdib.register_waveform_generator(HANDLES[1], st)
            sdcDevice.mdib.register_waveform_generator(HANDLES[2], si)

            time.sleep(3)
            self.assertGreater(len(testSubscr.reports), 20)
            report = testSubscr.reports[-1]  # a
            self._verify_proper_namespaces(report)
            in_report = ReceivedSoap12Envelope(report.as_xml())
            expected_action = sdcDevice.mdib.sdc_definitions.Actions.Waveform
            self.assertEqual(in_report.address.action, expected_action)

    def test_episodicMetricReportEvent(self):
        ''' verify that an event message is sent to subscriber and that message is valid'''
        # directly inject a subscription event, this test is not about starting subscriptions
        for sdcDevice in self._allDevices:
            testSubscr = mockstuff.TestDevSubscription(sdcDevice.mdib.sdc_definitions.Actions.EpisodicMetricReport,
                                                       sdcDevice.mdib.schema_validators)
            sdcDevice.subscriptions_manager._subscriptions.add_object(testSubscr)

            descriptorHandle = '0x34F00100'  # '0x34F04380'
            firstValue = 12
            with sdcDevice.mdib.transaction_manager() as mgr:
                # st = mgr.getMetricState(descriptorHandle)
                st = mgr.get_state(descriptorHandle)
                if st.MetricValue is None:
                    st.mk_metric_value()
                st.MetricValue.Value = firstValue
                st.Validity = 'Vld'
            self.assertEqual(len(testSubscr.reports), 1)
            response = testSubscr.reports[0]
            self._verify_proper_namespaces(response)
            print(response.as_xml(pretty=True))
            response.validate_body(sdcDevice.mdib.schema_validators.message_schema)

            # verify that header contains the identifier of client subscription
            env = ReceivedSoap12Envelope(response.as_xml())
            idents = env.header_node.findall(namespaces.wseTag('Identifier'))
            self.assertEqual(len(idents), 1)
            self.assertEqual(idents[0].text, mockstuff.TestDevSubscription.notifyRef)

    def test_episodicContextReportEvent(self):
        ''' verify that an event message is sent to subscriber and that message is valid'''
        # directly inject a subscription event, this test is not about starting subscriptions
        for sdcDevice in self._allDevices:
            testSubscr = mockstuff.TestDevSubscription(sdcDevice.mdib.sdc_definitions.Actions.EpisodicContextReport,
                                                       sdcDevice.mdib.schema_validators)
            sdcDevice.subscriptions_manager._subscriptions.add_object(testSubscr)
            patientContextDescriptor = sdcDevice.mdib.descriptions.NODETYPE.get_one(
                namespaces.domTag('PatientContextDescriptor'))
            descriptorHandle = patientContextDescriptor.handle
            with sdcDevice.mdib.transaction_manager() as mgr:
                st = mgr.get_state(descriptorHandle)
                st.CoreData.PatientType = pmtypes.PatientType.ADULT
            self.assertEqual(len(testSubscr.reports), 1)
            response = testSubscr.reports[0]
            self._verify_proper_namespaces(response)
            response.validate_body(sdcDevice.mdib.schema_validators.message_schema)

    def test_notifyOperation(self):
        for sdcDevice in self._allDevices:
            testSubscr = mockstuff.TestDevSubscription(sdcDevice.mdib.sdc_definitions.Actions.OperationInvokedReport,
                                                       sdcDevice.mdib.schema_validators)
            sdcDevice.subscriptions_manager._subscriptions.add_object(testSubscr)

            class DummyOperation:
                pass

            dummy_operation = DummyOperation()
            dummy_operation.handle = 'something'
            sdcDevice.subscriptions_manager.notify_operation(dummy_operation,
                                                             123,
                                                             pmtypes.InvocationState.FINISHED,
                                                             sdcDevice.mdib.nsmapper,
                                                             sequence_id='urn:uuid:abc',
                                                             mdib_version=1234,
                                                             error=pmtypes.InvocationError.UNSPECIFIED,
                                                             error_message='')
            self.assertEqual(len(testSubscr.reports), 1)
