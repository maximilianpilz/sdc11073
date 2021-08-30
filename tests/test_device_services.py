# -*- coding: utf-8 -*-
import unittest
import os
import uuid
from lxml import etree as etree_
import logging
import logging.handlers
from sdc11073.wsdiscovery import WSDiscoveryWhitelist
from sdc11073.location import SdcLocation
from sdc11073.namespaces import msgTag, domTag, nsmap
from sdc11073.namespaces import Prefixes
from sdc11073.pysoap.soapenvelope import WsAddress, Soap12Envelope, ReceivedSoap12Envelope
from sdc11073.definitions_sdc import SDC_v1_Definitions
from sdc11073.pmtypes import AlertConditionPriority
from tests import mockstuff

_sdc_ns = Prefixes.SDC.namespace


class TestDeviceServices(unittest.TestCase):
    
    def setUp(self):
        ''' validate test data'''
        print ('############### setUp {}... ##############'.format(self._testMethodName))
        self.wsDiscovery = WSDiscoveryWhitelist(['127.0.0.1'])
        self.wsDiscovery.start()
        my_uuid = None # let device create one
        self.sdcDevice_final = mockstuff.SomeDevice.from_mdib_file(self.wsDiscovery, my_uuid, '70041_MDIB_Final.xml')
        self.sdcDevice_final.start_all()
        self._alldevices = (self.sdcDevice_final,)
        print ('############### setUp done {} ##############'.format(self._testMethodName))


    def tearDown(self):
        print ('############### tearDown {}... ##############'.format(self._testMethodName))
        for d in self._alldevices:
            if d:
                d.stop_all()
        self.wsDiscovery.stop()
        print ('############### tearDown {} done ##############'.format(self._testMethodName))
    
    
    def _mkGetRequest(self, sdcDevice, porttype, method, endpoint_reference):
        if sdcDevice is self.sdcDevice_final:
            ns = sdcDevice.mdib.sdc_definitions.DPWS_SDCNamespace
        else:
            ns = sdcDevice.mdib.sdc_definitions.MessageModelNamespace
        action = '{}/{}/{}'.format(ns, porttype, method)
        body_node = etree_.Element(msgTag(method))
        soapEnvelope = Soap12Envelope(Prefixes.partial_map(Prefixes.S12, Prefixes.WSA, Prefixes.MSG))
        identifier = uuid.uuid4().urn
        soapEnvelope.add_header_object(WsAddress(message_id=identifier,
                                               action=action, 
                                               addr_to=endpoint_reference))
        soapEnvelope.add_body_element(body_node)
                
        soapEnvelope.validate_body(sdcDevice.mdib.biceps_schema.message_schema)
        return soapEnvelope


    def test_dispatch_final(self):
        self._test_dispatch(self.sdcDevice_final)

    def _test_dispatch(self, sdcDevice):
        dispatcher = sdcDevice._handler._http_server_thread.devices_dispatcher

        endpoint_reference = sdcDevice._handler._get_dispatcher.hosting_service.epr
        getService = sdcDevice._handler._get_dispatcher
        getEnv = self._mkGetRequest(sdcDevice, getService.port_type_string, 'GetMdib', endpoint_reference)
        http_header = {}
        response_string = dispatcher.on_post(endpoint_reference, http_header, getEnv.as_xml())
        self.assertTrue('/{}/GetMdibResponse'.format(getService.port_type_string).encode('utf-8') in response_string)

        endpoint_reference = sdcDevice._handler._context_dispatcher.hosting_service.epr
        contextService = sdcDevice._handler._context_dispatcher
        getEnv = self._mkGetRequest(sdcDevice, contextService.port_type_string, 'GetContextStates', endpoint_reference)
        http_header = {}
        response_string = dispatcher.on_post(endpoint_reference, http_header, getEnv.as_xml())
        self.assertTrue('/{}/GetContextStatesResponse'.format(contextService.port_type_string).encode('utf-8') in response_string)


    def test_getMdib(self):
        for sdcDevice in self._alldevices:
            getService = sdcDevice._handler._get_dispatcher
            endpoint_reference = '123'
            getEnv = self._mkGetRequest(sdcDevice, getService.port_type_string, 'GetMdib', endpoint_reference)
            receivedEnv = ReceivedSoap12Envelope(getEnv.as_xml())
            http_header = {}
            response = getService._on_get_mdib(http_header, receivedEnv)
            response.validate_body(sdcDevice.mdib.biceps_schema.message_schema)

    def test_getMdState(self):
        for sdcDevice in self._alldevices:
            getService = sdcDevice._handler._get_dispatcher
            endpoint_reference = '123'
            getEnv = self._mkGetRequest(sdcDevice, getService.port_type_string, 'GetMdState', endpoint_reference)
            receivedEnv = ReceivedSoap12Envelope(getEnv.as_xml())
            http_header = {}
            response = getService.dispatch_soap_request(None, http_header, receivedEnv)
            response.validate_body(sdcDevice.mdib.biceps_schema.message_schema)
   

    def test_getMdDescription(self):
        for sdcDevice in self._alldevices:
            getService = sdcDevice._handler._get_dispatcher
            endpoint_reference = '123'
            getEnv = self._mkGetRequest(sdcDevice, getService.port_type_string, 'GetMdDescription', endpoint_reference)
            receivedEnv = ReceivedSoap12Envelope(getEnv.as_xml())
            http_header = {}
            response = getService.dispatch_soap_request(None, http_header, receivedEnv)
            
            response.validate_body(sdcDevice.mdib.biceps_schema.message_schema)


    def test_changeAlarmPrio(self):
        ''' This is a test for defect SDCSIM-129
        The order of children of '''
        for sdcDevice in self._alldevices:
            getService = sdcDevice._handler._get_dispatcher
            endpoint_reference = '123'
            with sdcDevice.mdib.transaction_manager() as tr:
                alarmConditionDescriptor = tr.get_descriptor('0xD3C00109')
                alarmConditionDescriptor.Priority = AlertConditionPriority.LOW
            getEnv = self._mkGetRequest(sdcDevice, getService.port_type_string, 'GetMdDescription', endpoint_reference)
            receivedEnv = ReceivedSoap12Envelope(getEnv.as_xml())
            http_header = {}
            response = getService.dispatch_soap_request(None, http_header, receivedEnv)
            response.validate_body(sdcDevice.mdib.biceps_schema.message_schema)


    def test_getContextStates(self):
        facility = 'HOSP42'
        poc = 'Care Unit 1'
        bed = 'my bed'
        loc = SdcLocation(fac=facility, poc=poc, bed=bed)
        for sdcDevice in self._alldevices:
            sdcDevice.mdib.set_location(loc)
            contextService = sdcDevice._handler._context_dispatcher
            endpoint_reference = '123'
            getEnv = self._mkGetRequest(sdcDevice, contextService.port_type_string, 'GetContextStates', endpoint_reference)
            receivedEnv = ReceivedSoap12Envelope(getEnv.as_xml())
            http_header = {}
            response = contextService.dispatch_soap_request(None, http_header, receivedEnv)
            print (response.as_xml(pretty=True))
            response.validate_body(sdcDevice.mdib.biceps_schema.message_schema)
            _ns = sdcDevice.mdib.nsmapper # shortcut
            query = '*/{}[@{}="{}"]'.format(_ns.doc_name(Prefixes.MSG, 'ContextState'),
                                          _ns.doc_name(Prefixes.XSI,'type'),
                                          _ns.doc_name(Prefixes.PM,'LocationContextState'))
            locationContextNodes = response.body_node.xpath(query, namespaces=_ns.doc_ns_map)
            self.assertEqual(len(locationContextNodes), 1)
            identificationNode = locationContextNodes[0].find(domTag('Identification'))
            if sdcDevice is self.sdcDevice_final:
                self.assertEqual(identificationNode.get('Extension'), '{}///{}//{}'.format(facility, poc, bed))
            else:
                self.assertEqual(identificationNode.get('Extension'), '{}/{}/{}'.format(facility, poc, bed))
            
            locationDetailNode = locationContextNodes[0].find(domTag('LocationDetail'))
            self.assertEqual(locationDetailNode.get('PoC'), poc) 
            self.assertEqual(locationDetailNode.get('Bed'), bed) 
            self.assertEqual(locationDetailNode.get('Facility'), facility) 
            print (response.as_xml(pretty=True))


    def test_wsdl_final(self):
        '''
        check porttype and action namespaces in wsdl
        '''
        dev = self.sdcDevice_final
        for hosted in dev._handler._hosted_services:
            wsdl = etree_.fromstring(hosted._wsdl_string)
            inputs = wsdl.xpath('//wsdl:input', namespaces=nsmap)#{'wsdl':'http://schemas.xmlsoap.org/wsdl/'})
            outputs = wsdl.xpath('//wsdl:output', namespaces=nsmap)#{'wsdl':'http://schemas.xmlsoap.org/wsdl/'})
            self.assertGreater(len(inputs), 0)
            self.assertGreater(len(outputs), 0)
            for src in (inputs, outputs):
                for i in inputs:
                    action_keys = [ k for k in i.attrib.keys() if k.endswith('Action')]
                    for k in action_keys:
                        action = i.attrib[k]
                        self.assertTrue(action.startswith(SDC_v1_Definitions.ActionsNamespace))


    def test_metadata_final(self):
        '''
        verifies that
        - 7 hosted services exist ( one per port type)
        - every port type has BICEPS Message Model as namespace
        '''
        dev = self.sdcDevice_final
        metadata_node = dev._handler._mk_metadata_node()
        print (etree_.tostring(metadata_node))
        dpws_hosted = metadata_node.xpath('//dpws:Hosted', namespaces={'dpws': 'http://docs.oasis-open.org/ws-dd/ns/dpws/2009/01'})
        self.assertEqual(len(dpws_hosted), 4) #
        for h in dpws_hosted:
            dpws_types = h.xpath('dpws:Types', namespaces={'dpws': 'http://docs.oasis-open.org/ws-dd/ns/dpws/2009/01'})
            for t in dpws_types:
                txt = t.text
                port_types = txt.split()
                for p in port_types:
                    ns, value = p.split(':')
                    self.assertEqual(metadata_node.nsmap[ns], _sdc_ns)


def suite():
    return unittest.TestLoader().loadTestsFromTestCase(TestDeviceServices)



if __name__ == '__main__':
    def mklogger(logFolder):
        applog = logging.getLogger('sdc')
        if len(applog.handlers) == 0:
            
            ch = logging.StreamHandler()
            # create formatter
            formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            # add formatter to ch
            ch.setFormatter(formatter)
            # add ch to logger
            applog.addHandler(ch)
            ch2 = logging.handlers.RotatingFileHandler(os.path.join(logFolder,'sdcdevice.log'),
                                                       maxBytes=100000000,
                                                       backupCount=100)
            ch2.setFormatter(formatter)
            # add ch to logger
            applog.addHandler(ch2)
        
        applog.setLevel(logging.DEBUG)
        # reduce log level for some loggers
        tmp = logging.getLogger('sdc.discover')
        tmp.setLevel(logging.WARN)
        tmp = logging.getLogger('sdc.client.subscr')
        tmp.setLevel(logging.INFO)
        tmp = logging.getLogger('sdc.client.mdib')
        tmp.setLevel(logging.INFO)
        tmp = logging.getLogger('sdc.client.wf')
        tmp.setLevel(logging.INFO)
        tmp = logging.getLogger('sdc.client.Set')
        tmp.setLevel(logging.INFO)
        tmp = logging.getLogger('sdc.client.Get')
        tmp.setLevel(logging.DEBUG)
        tmp = logging.getLogger('sdc.device')
        tmp.setLevel(logging.DEBUG)
        tmp = logging.getLogger('sdc.device.subscrMgr')
        tmp.setLevel(logging.DEBUG)
        logging.getLogger('sdc.device.GetService').setLevel(logging.DEBUG)
        
        
        return applog


    mklogger('c:/tmp')
#     unittest.TextTestRunner(verbosity=2).run(suite())
#    unittest.TextTestRunner(verbosity=2).run(unittest.TestLoader().loadTestsFromName('test_device_services.TestDeviceServices.test_getMdib'))
#    unittest.TextTestRunner(verbosity=2).run(unittest.TestLoader().loadTestsFromName('test_device_services.TestDeviceServices.test_getContextStates'))
#    unittest.TextTestRunner(verbosity=2).run(unittest.TestLoader().loadTestsFromName('test_device_services.TestDeviceServices.test_getMdDescription'))
    unittest.TextTestRunner(verbosity=2).run(unittest.TestLoader().loadTestsFromName('test_device_services.TestDeviceServices.test_changeAlarmPrio'))
