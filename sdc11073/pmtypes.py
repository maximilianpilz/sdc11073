""" Implementation of data types used in Participant Model"""
import enum
import inspect
import sys
import traceback
from decimal import Decimal
from math import isclose
from dataclasses import dataclass
from lxml import etree as etree_
from typing import Optional, Union
from .mdib import containerproperties as cp
from .namespaces import QN_TYPE, text_to_qname
from . import pm_qnames as pm


class StringEnum(str, enum.Enum):

    def __str__(self):
        return str(self.value)


class PropertyBasedPMTypeMeta(type):
    def __new__(mcs, name, bases, class_dict):
        class_ = super().__new__(mcs, name, bases, class_dict)
        class_.freedom = True
        return class_


class PropertyBasedPMType:
    """ Base class that assumes all data is defined as containerproperties and _props lists all property names."""

    def as_etree_node(self, qname, nsmap):
        node = etree_.Element(qname, nsmap=nsmap)
        self.update_node(node)
        return node

    def update_node(self, node):
        for prop_name, prop in self.sorted_container_properties():
            try:
                prop.update_xml_value(self, node)
            except Exception as ex:
                # re-raise with some information about the data
                raise ValueError(
                    f'In {self.__class__.__name__}.{prop_name}, {str(prop)} could not update: {traceback.format_exc()}') from ex

    def update_from_node(self, node):
        for dummy, prop in self.sorted_container_properties():
            prop.update_from_node(self, node)

    def sorted_container_properties(self):
        """
        @return: a list of (name, object) tuples of all GenericProperties ( and subclasses)
        list is created based on _props lists of classes
        """
        ret = []
        classes = inspect.getmro(self.__class__)
        for cls in reversed(classes):
            try:
                names = cls._props  # pylint: disable=protected-access
            except AttributeError:
                continue
            for name in names:
                obj = getattr(cls, name)
                if obj is not None:
                    ret.append((name, obj))
        return ret

    def __eq__(self, other):
        """ compares all properties"""
        try:
            for name, dummy in self.sorted_container_properties():
                my_value = getattr(self, name)
                other_value = getattr(other, name)
                if my_value == other_value:
                    continue
                if (isinstance(my_value, float) or isinstance(other_value, float)) and isclose(my_value, other_value):
                    continue  # float compare (almost equal)
                return False
            return True
        except (TypeError, AttributeError):
            return False

    def __ne__(self, other):
        return not self == other

    def __repr__(self):
        return f'{self.__class__.__name__}({self.sorted_container_properties()})'

    @classmethod
    def from_node(cls, node):
        """ default from_node Constructor that provides no arguments for class __init__"""
        obj = cls()
        obj.update_from_node(node)
        return obj

    @classmethod
    def value_class_from_node(cls, node):
        """ If node has an xsi:Type attribute, return the class that reflects that type"""
        xsi_type_str = node.get(QN_TYPE)
        if xsi_type_str is None:
            return cls
        xsi_type = text_to_qname(xsi_type_str, node.nsmap)
        return _get_pmtypes_class(xsi_type)


class ElementWithTextOnly(PropertyBasedPMType):
    text = cp.NodeTextProperty()  # this is the text of the node. Here attribute is lower case!
    _props = ['text']
    '''An Element that has no attributes, only a text.'''

    def __init__(self, text):
        self.text = text

    @classmethod
    def from_node(cls, node):
        text = node.text
        return cls(text)


class T_TextWidth(StringEnum):  # pylint: disable=invalid-name
    XS = 'xs'
    S = 's'
    M = 'm'
    L = 'l'
    XL = 'xl'
    XXL = 'xxl'


class LocalizedText(PropertyBasedPMType):
    text = cp.LocalizedTextContentProperty()  # this is the text of the node. Here attribute is lower case!
    # pylint: disable=invalid-name
    Ref = cp.LocalizedTextRefAttributeProperty('Ref')
    Lang = cp.StringAttributeProperty('Lang')
    Version = cp.ReferencedVersionAttributeProperty('Version')
    TextWidth = cp.EnumAttributeProperty('TextWidth', enum_cls=T_TextWidth)
    # pylint: enable=invalid-name
    _props = ['text', 'Ref', 'Lang', 'Version', 'TextWidth']
    ''' Represents a LocalizedText type in the Participant Model. '''

    def __init__(self, text, lang=None, ref=None, version=None, textWidth=None):
        """
        :param text: a string
        :param lang: a string or None
        :param ref: a string or None
        :param version: an int or None
        :param textWidth: xs, s, m, l, xl, xxl or None
        """
        self.text = text
        # pylint: disable=invalid-name
        self.Lang = lang
        self.Ref = ref
        self.Version = version
        self.TextWidth = textWidth
        # pylint: enable=invalid-name

    @classmethod
    def from_node(cls, node):
        text = node.text
        lang = node.get('Lang')
        ref = node.get('Ref')
        version = node.get('Version')
        if version is not None:
            version = int(version)
        text_width = node.get('TextWidth')
        enum_text_width = T_TextWidth(text_width) if text_width is not None else None
        return cls(text, lang, ref, version, enum_text_width)


DEFAULT_CODING_SYSTEM = 'urn:oid:1.2.840.10004.1.1.1.0.0.1'  # ISO/IEC 11073-10101



@dataclass(frozen=True)
class Coding:
    code: str
    coding_system: Optional[str] = DEFAULT_CODING_SYSTEM
    coding_system_version: Optional[str] = None

    def __post_init__(self):
        """Previous versions allowed also an int as code. This exception makes it easier to detect wrong type."""
        if not isinstance(self.code, str):
            raise TypeError('code must be a string!')

    def equals(self, other):
        """ different compare method to __eq__, overwriting __eq__ makes Coding un-hashable!
        :param other: Coding or CodedValue to compare with
        :return: boolean
        """
        return have_matching_codes(self, other)

    @classmethod
    def from_node(cls, node):
        """ Read Code and CodingSystem attributes of a node (CodedValue). """
        code = node.get('Code')
        coding_system = node.get('CodingSystem', DEFAULT_CODING_SYSTEM)
        coding_system_version = node.get('CodingSystemVersion')
        return cls(code, coding_system, coding_system_version)


class T_Translation(PropertyBasedPMType):
    """
    Translation is part of CodedValue in BICEPS FINAL
    """
    # pylint: disable=invalid-name
    ext_Extension = cp.ExtensionNodeProperty()
    Code = cp.CodeIdentifierAttributeProperty('Code', is_optional=False)
    CodingSystem = cp.StringAttributeProperty('CodingSystem', implied_py_value=DEFAULT_CODING_SYSTEM)
    CodingSystemVersion = cp.StringAttributeProperty('CodingSystemVersion')
    # pylint: enable=invalid-name

    _props = ['ext_Extension', 'Code', 'CodingSystem', 'CodingSystemVersion']

    def __init__(self, code: str,
                 coding_system: Optional[str] = None,
                 coding_system_version: Optional[str] = None):
        """
        :param code: a string
        :param codingSystem: anyURI or None, defaults to ISO/IEC 11073-10101 if None
        :param codingSystemVersion: optional string, min. length = 1
        """
        # pylint: disable=invalid-name
        if not isinstance(code, str):
            raise TypeError('code must be a string!')
        self.Code = code
        self.CodingSystem = coding_system
        self.CodingSystemVersion = coding_system_version
        # pylint: enable=invalid-name

    @property
    def coding(self):
        return Coding(self.Code, self.CodingSystem, self.CodingSystemVersion)

    def __repr__(self):
        if self.CodingSystem is None:
            return f'CodedValue({self.Code})'
        if self.CodingSystemVersion is None:
            return f'CodedValue({self.Code}, codingsystem={self.CodingSystem})'
        return f'CodedValue({self.Code}, codingsystem={self.CodingSystem}, codingsystemversion={self.CodingSystemVersion})'

    @classmethod
    def from_node(cls, node):
        obj = cls(None)
        obj.update_from_node(node)
        return obj


class CodedValue(PropertyBasedPMType):
    # pylint: disable=invalid-name
    ext_Extension = cp.ExtensionNodeProperty()
    CodingSystemName = cp.SubElementListProperty(pm.CodingSystemName, value_class=LocalizedText)
    ConceptDescription = cp.SubElementListProperty(pm.ConceptDescription, value_class=LocalizedText)
    Translation = cp.SubElementListProperty(pm.Translation, value_class=T_Translation)
    Code = cp.CodeIdentifierAttributeProperty('Code', is_optional=False)
    CodingSystem = cp.StringAttributeProperty('CodingSystem', implied_py_value=DEFAULT_CODING_SYSTEM)
    CodingSystemVersion = cp.StringAttributeProperty('CodingSystemVersion')
    SymbolicCodeName = cp.SymbolicCodeNameAttributeProperty('SymbolicCodeName')
    # pylint: enable=invalid-name
    _props = ['ext_Extension', 'CodingSystemName', 'ConceptDescription', 'Translation',
              'Code', 'CodingSystem', 'CodingSystemVersion', 'SymbolicCodeName']

    def __init__(self, code, coding_system=None, coding_system_version=None, coding_system_names=None,
                 concept_descriptions=None, symbolic_code_name=None):
        """
        :param code: a string
        :param codingSystem: anyURI or None, defaults to ISO/IEC 11073-10101 if None
        :param codingSystemVersion: a string, min. length = 1
        :param codingSystemNames: a list of LocalizedText objects or None
        :param conceptDescriptions: a list of LocalizedText objects or None
        :param symbolicCodeName: a string, min. length = 1 or None
        """
        # pylint: disable=invalid-name
        if code is not None and not isinstance(code, str):
            raise TypeError('code must be a string!')
        self.Code = code
        self.CodingSystem = coding_system
        self.CodingSystemVersion = coding_system_version
        self.CodingSystemName = [] if coding_system_names is None else coding_system_names
        self.ConceptDescription = [] if concept_descriptions is None else concept_descriptions
        self.SymbolicCodeName = symbolic_code_name
        # pylint: enable=invalid-name

    @property
    def coding(self):
        return Coding(self.Code, self.CodingSystem, self.CodingSystemVersion)

    @property
    def all_codings(self):
        ret = [self.coding]
        if self.Translation is not None:
            ret.extend([t.coding for t in self.Translation])
        return ret

    def __repr__(self):
        if self.CodingSystem is None:
            return f'CodedValue({self.Code})'
        if self.CodingSystemVersion is None:
            return f'CodedValue({self.Code}, codingsystem="{self.CodingSystem}")'
        return f'CodedValue({self.Code}, codingsystem="{self.CodingSystem}", codingsystemversion="{self.CodingSystemVersion}")'

    def equals(self, other):
        """
        Compare this CodedValue with another one or a Coding.
        A simplified compare with an int or string is also possible, it assumes DEFAULT_CODING_SYSTEM and no CodingSystemVersion
        :param other: another CodedValue or Coding
        :return: boolean
        """
        return have_matching_codes(self, other)

    @classmethod
    def from_node(cls, node):
        obj = cls(None)
        obj.update_from_node(node)
        return obj


def have_matching_codes(code_a: Union[CodedValue, Coding], code_b: Union[CodedValue, Coding]):
    try:
        codes_a = set(code_a.all_codings)
    except AttributeError:
        codes_a = set([code_a])
    try:
        codes_b = set(code_b.all_codings)
    except AttributeError:
        codes_b = set([code_b])
    common_codes = codes_a.intersection(codes_b)
    return len(common_codes) > 0


class Annotation(PropertyBasedPMType):
    # pylint: disable=invalid-name
    ext_Extension = cp.ExtensionNodeProperty()
    Type = cp.SubElementProperty(pm.Type, value_class=CodedValue)
    # pylint: enable=invalid-name
    _props = ['ext_Extension', 'Type']

    codedValue = Type
    ''' An Annotation contains a Type Element that is a CodedValue.
    This is intended as an immutable object. After it has been created, no modification shall be done. '''

    def __init__(self, coded_value):
        self.Type = coded_value  # pylint: disable=invalid-name
        self.coding = coded_value.coding

    @classmethod
    def from_node(cls, node):
        type_node = node.find(pm.Type)
        coded_value = CodedValue.from_node(type_node)
        return cls(coded_value)


class OperatingMode(StringEnum):
    DISABLED = 'Dis'
    ENABLED = 'En'
    NA = 'NA'


class OperationGroup(PropertyBasedPMType):
    # pylint: disable=invalid-name
    ext_Extension = cp.ExtensionNodeProperty()
    Type = cp.SubElementProperty(pm.Type, value_class=CodedValue)
    OperatingMode = cp.EnumAttributeProperty('OperatingMode', enum_cls=OperatingMode)
    Operations = cp.OperationRefListAttributeProperty('Operations')
    # pylint: enable=invalid-name
    _props = ['ext_Extension', 'Type', 'OperatingMode', 'Operations']

    def __init__(self, coded_value=None, operating_mode=None, operations=None):
        """
        :param coded_value: a CodedValue instances or None
        :param operating_mode:  xsd:string string
        :param operations: a xsd:string
        """
        # pylint: disable=invalid-name
        self.Type = coded_value
        self.OperatingMode = operating_mode
        self.Operations = operations
        # pylint: enable=invalid-name

    @classmethod
    def from_node(cls, node):
        type_node = node.find(pm.Type)
        coded_value = CodedValue.from_node(type_node)
        operating_mode = cls.OperatingMode.get_py_value_from_node(None, node)
        operations = cls.Operations.get_py_value_from_node(None, node)
        ret = cls(coded_value, operating_mode, operations)
        # ret.node = node
        return ret


class InstanceIdentifier(PropertyBasedPMType):
    # pylint: disable=invalid-name
    ext_Extension = cp.ExtensionNodeProperty()
    Type = cp.SubElementProperty(pm.Type, value_class=CodedValue)
    IdentifierName = cp.SubElementListProperty(pm.IdentifierName, value_class=LocalizedText)
    Root = cp.AnyURIAttributeProperty('Root',
                                      default_py_value='biceps.uri.unk')  # xsd:anyURI string, default is defined in R0135
    Extension = cp.ExtensionAttributeProperty('Extension')  # a xsd:string
    # pylint: enable=invalid-name
    _props = ('ext_Extension', 'Type', 'IdentifierName', 'Root', 'Extension')

    def __init__(self, root, type_coded_value=None, identifier_names=None, extension_string=None):
        """
        :param root:  xsd:anyURI string
        :param type_coded_value: a CodedValue instances or None
        :param identifier_names: a list of LocalizedText instances or None
        :param extension_string: a xsd:string
        """
        # pylint: disable=invalid-name
        self.Root = root
        self.Type = type_coded_value
        self.IdentifierName = [] if identifier_names is None else identifier_names
        self.Extension = extension_string
        # pylint: enable=invalid-name
        self.node = None

    @classmethod
    def from_node(cls, node):
        ret = cls(None, None, None, None)
        ret.update_from_node(node)
        ret.node = node
        return ret

    def __repr__(self):
        return f'InstanceIdentifier(root={self.Root!r}, Type={self.Type} ext={self.Extension!r})'


class OperatingJurisdiction(InstanceIdentifier):
    NODETYPE = pm.OperatingJurisdiction


class Range(PropertyBasedPMType):
    # pylint: disable=invalid-name
    Extension = cp.ExtensionNodeProperty()
    Lower = cp.DecimalAttributeProperty('Lower')  # optional, an integer or float
    Upper = cp.DecimalAttributeProperty('Upper')  # optional, an integer or float
    StepWidth = cp.DecimalAttributeProperty('StepWidth')  # optional, an integer or float
    RelativeAccuracy = cp.DecimalAttributeProperty('RelativeAccuracy')  # optional, an integer or float
    AbsoluteAccuracy = cp.DecimalAttributeProperty('AbsoluteAccuracy')  # optional, an integer or float
    # pylint: enable=invalid-name
    _props = ['Extension', 'Lower', 'Upper', 'StepWidth', 'RelativeAccuracy', 'AbsoluteAccuracy']

    def __init__(self, lower=None, upper=None, step_width=None, relative_accuracy=None, absolute_accuracy=None):
        """
        :param lower: The including lower bound of the range. A value as float or integer, can be None
        :param upper: The including upper bound of the range. A value as float or integer, can be None
        :param step_width: The numerical distance between two values in the range of the given upper and lower bound. A value as float or integer, can be None
        :param relative_accuracy: Maximum relative error in relation to the correct value within the given range. A value as float or integer, can be None
        :param absolute_accuracy: Maximum absolute error in relation to the correct value within the given range. A value as float or integer, can be None
        """
        # pylint: disable=invalid-name
        self.Lower = lower
        self.Upper = upper
        self.StepWidth = step_width
        self.RelativeAccuracy = relative_accuracy
        self.AbsoluteAccuracy = absolute_accuracy
        # pylint: enable=invalid-name

    def __repr__(self):
        return f'Range (Lower={self.Lower!r}, Upper={self.Upper!r}, StepWidth={self.StepWidth!r}, ' \
               f'RelativeAccuracy={self.RelativeAccuracy!r}, AbsoluteAccuracy={self.AbsoluteAccuracy!r})'


class Measurement(PropertyBasedPMType):
    # pylint: disable=invalid-name
    ext_Extension = cp.ExtensionNodeProperty()
    MeasurementUnit = cp.SubElementProperty(pm.MeasurementUnit, value_class=CodedValue)
    MeasuredValue = cp.DecimalAttributeProperty('MeasuredValue', is_optional=False)
    # pylint: enable=invalid-name
    _props = ['ext_Extension', 'MeasurementUnit', 'MeasuredValue']

    def __init__(self, value, unit):
        """
        :param value: a value as string, float or integer
        :param unit: a CodedValue instance
        """
        # pylint: disable=invalid-name
        self.MeasuredValue = value
        self.MeasurementUnit = unit
        # pylint: enable=invalid-name

    @classmethod
    def from_node(cls, node):
        value = node.get('MeasuredValue')
        if value is not None:
            value = Decimal(value)
        unit = None
        unit_node = node.find(pm.MeasurementUnit)
        if unit_node is not None:
            unit = CodedValue.from_node(unit_node)
        return cls(value, unit)

    def __repr__(self):
        return f'Measurement(value={self.MeasuredValue!r}, Unit={self.MeasurementUnit!r})'


class AllowedValue(PropertyBasedPMType):
    # pylint: disable=invalid-name
    Value = cp.NodeTextProperty(pm.Value)
    Type = cp.SubElementProperty(pm.Type, value_class=CodedValue)
    # pylint: enable=invalid-name
    _props = ['Value', 'Type']
    type_coding = Type
    value = Value

    def __init__(self, value_string, type_coding=None):
        """One AllowedValue of a EnumStringMetricDescriptor. It has up to two sub elements "Value" and "Type"(optional).
        A StringEnumMetricDescriptor has a list of AllowedValues.
        :param value_string: a string
        :param type_coding: an optional CodedValue instance
        """
        # pylint: disable=invalid-name
        self.Value = value_string
        self.Type = type_coding
        # pylint: enable=invalid-name

    @classmethod
    def from_node(cls, node):
        value_string = node.find(pm.Value).text
        type_node = node.find(pm.Type)
        if type_node is None:
            type_coding = None
        else:
            type_coding = CodedValue.from_node(type_node)
        return cls(value_string, type_coding)


class MeasurementValidity(StringEnum):
    """Level of validity of a measured value."""
    VALID = 'Vld'
    VALIDATED_DATA = 'Vldated'
    MEASUREMENT_ONGOING = 'Ong'
    QUESTIONABLE = 'Qst'
    CALIBRATION_ONGOING = 'Calib'
    INVALID = 'Inv'
    OVERFLOW = 'Oflw'
    UNDERFLOW = 'Uflw'
    NA = 'NA'


class GenerationMode(StringEnum):
    """Describes whether METRIC data is generated by real measurements or under unreal settings (demo or test data)."""
    REAL = 'Real'  # Real Data. A value that is generated under real conditions
    TEST = 'Test'  # Test Data. A value that is arbitrary and is for testing purposes only
    DEMO = 'Demo'  # Demo Data. A value that is arbitrary and is for demonstration purposes only


class T_MetricQuality(PropertyBasedPMType):
    # pylint: disable=invalid-name
    Validity = cp.EnumAttributeProperty('Validity', enum_cls=MeasurementValidity)
    Mode = cp.EnumAttributeProperty('Mode', implied_py_value='Real', enum_cls=GenerationMode)
    Qi = cp.QualityIndicatorAttributeProperty('Qi', implied_py_value=1)
    # pylint: enable=invalid-name
    _props = ('Validity', 'Mode', 'Qi')

    def __init__(self):
        pass


class AbstractMetricValue(PropertyBasedPMType):
    """ This is the base class for metric values inside metric states"""
    # pylint: disable=invalid-name
    ext_Extension = cp.ExtensionNodeProperty()
    StartTime = cp.TimestampAttributeProperty('StartTime')
    StopTime = cp.TimestampAttributeProperty('StopTime')
    DeterminationTime = cp.TimestampAttributeProperty('DeterminationTime')
    MetricQuality = cp.SubElementProperty(pm.MetricQuality, value_class=T_MetricQuality)
    Annotation = cp.SubElementListProperty(pm.Annotation, Annotation)
    Annotations = Annotation  # alternative name that makes it clearer that this is a list
    # pylint: enable=invalid-name
    _props = ('ext_Extension', 'StartTime', 'StopTime', 'DeterminationTime', 'MetricQuality', 'Annotation')

    def __init__(self, node=None):
        # attributes of root node
        self.node = node
        self.MetricQuality = T_MetricQuality()  # pylint: disable=invalid-name
        if node is not None:
            self.update_from_node(node)
        else:
            # mandatory value, for convenience it is preset
            self.MetricQuality.Validity = MeasurementValidity.VALID  # pylint: disable=invalid-name

    def update_from_node(self, node):
        for dummy, prop in self.sorted_container_properties():
            prop.update_from_node(self, node)
        self.node = node

    def as_etree_node(self, qname, nsmap):
        node = super().as_etree_node(qname, nsmap)
        return node

    @classmethod
    def from_node(cls, node):
        obj = cls(node)
        return obj


class NumericMetricValue(AbstractMetricValue):
    # pylint: disable=invalid-name
    NODETYPE = pm.NumericMetricValue
    Value = cp.DecimalAttributeProperty('Value')  # an integer or float
    # pylint: enable=invalid-name
    _props = ('Value',)

    def __repr__(self):
        return f'{self.__class__.__name__} Validity={self.MetricQuality.Validity}' \
               f' Value={self.Value} DeterminationTime={self.DeterminationTime}'


class StringMetricValue(AbstractMetricValue):
    # pylint: disable=invalid-name
    NODETYPE = pm.StringMetricValue
    Value = cp.StringAttributeProperty('Value')  # a string
    # pylint: enable=invalid-name
    _props = ('Value',)

    def __repr__(self):
        return f'{self.__class__.__name__} Validity={self.MetricQuality.Validity} ' \
               f'Value={self.Value} DeterminationTime={self.DeterminationTime}'


class ApplyAnnotation(PropertyBasedPMType):
    # pylint: disable=invalid-name
    AnnotationIndex = cp.UnsignedIntAttributeProperty('AnnotationIndex', is_optional=False)
    SampleIndex = cp.UnsignedIntAttributeProperty('SampleIndex', is_optional=False)
    # pylint: enable=invalid-name
    _props = ['AnnotationIndex', 'SampleIndex']

    def __init__(self, annotationIndex=None, sampleIndex=None):
        # pylint: disable=invalid-name
        self.AnnotationIndex = annotationIndex
        self.SampleIndex = sampleIndex
        # pylint: enable=invalid-name

    @classmethod
    def from_node(cls, node):
        obj = cls(None, None)
        cls.AnnotationIndex.update_from_node(obj, node)
        cls.SampleIndex.update_from_node(obj, node)
        return obj

    def __repr__(self):
        return f'{self.__class__.__name__}(AnnotationIndex={self.AnnotationIndex}, SampleIndex={self.SampleIndex})'


class SampleArrayValue(AbstractMetricValue):
    # pylint: disable=invalid-name
    NODETYPE = pm.SampleArrayValue
    Samples = cp.DecimalListAttributeProperty('Samples')  # list of xs:decimal types
    ApplyAnnotation = cp.SubElementListProperty(pm.ApplyAnnotation, ApplyAnnotation)
    ApplyAnnotations = ApplyAnnotation  # alternative name that makes it clearer that this is a list
    # pylint: enable=invalid-name
    _props = ('Samples', 'ApplyAnnotation')

    def __repr__(self):
        return f'{self.__class__.__name__} Samples={self.Samples} ApplyAnnotations={self.ApplyAnnotations}'


class RemedyInfo(PropertyBasedPMType):
    """An Element that has
         0..1 Subelement "Extension" (not handled here)
         0..n SubElements "Description" type=pm:LocalizedText."""
    # pylint: disable=invalid-name
    ext_Extension = cp.ExtensionNodeProperty()
    Description = cp.SubElementListProperty(pm.Description, value_class=LocalizedText)
    # pylint: enable=invalid-name
    _props = ['ext_Extension', 'Description']

    def __init__(self, descriptions=None):
        """
        :param descriptions : a list of LocalizedText objects or None
        """
        if descriptions:
            self.Description = descriptions  # pylint: disable=invalid-name


class CauseInfo(PropertyBasedPMType):
    """An Element that has
         0..1 Subelement "RemedyInfo", type = pm:RemedyInfo
         0..n SubElements "Description" type=pm:LocalizedText."""
    # pylint: disable=invalid-name
    ext_Extension = cp.ExtensionNodeProperty()
    RemedyInfo = cp.SubElementProperty(pm.RemedyInfo, value_class=RemedyInfo)
    Description = cp.SubElementListProperty(pm.Description, value_class=LocalizedText)
    # pylint: enable=invalid-name
    _props = ['ext_Extension', 'RemedyInfo', 'Description']

    def __init__(self, remedyInfo=None, descriptions=None):
        """
        :param remedyInfo: a RemedyInfo instance or None
        :param descriptions : a list of LocalizedText objects or None
        """
        # pylint: disable=invalid-name
        self.RemedyInfo = remedyInfo
        self.Description = descriptions or []
        # pylint: enable=invalid-name

    @classmethod
    def from_node(cls, node):
        remedy_info_node = node.find(pm.RemedyInfo)
        if remedy_info_node is not None:
            remedy_info = RemedyInfo.from_node(remedy_info_node)
        else:
            remedy_info = None
        descriptions = []
        for description_node in node.findall(pm.Description):
            descriptions.append(LocalizedText.from_node(description_node))
        return cls(remedy_info, descriptions)


class ActivateOperationDescriptorArgument(PropertyBasedPMType):
    """Argument for ActivateOperationDescriptor.
         1 Subelement "ArgName", type = pm:CodedValue
         1 SubElement "Arg" type=QName."""
    # pylint: disable=invalid-name
    ArgName = cp.SubElementProperty(pm.ArgName, value_class=CodedValue, is_optional=False)
    Arg = cp.NodeTextQNameProperty(pm.Arg, is_optional=False)
    # pylint: enable=invalid-name
    _props = ['ArgName', 'Arg']

    def __init__(self, arg_name=None, arg=None):
        """
        :param arg_name: a CodedValue instance
        :param arg : etree_.QName instance
        """
        # pylint: disable=invalid-name
        self.ArgName = arg_name
        self.Arg = arg
        # pylint: enable=invalid-name

    @classmethod
    def from_node(cls, node):
        arg_name_node = node.find(pm.ArgName)
        arg_name = CodedValue.from_node(arg_name_node)
        arg_node = node.find(pm.Arg)
        arg_qname = text_to_qname(arg_node.text, node.nsmap)
        return cls(arg_name, arg_qname)

    def __repr__(self):
        return f'{self.__class__.__name__}(argName={self.ArgName}, arg={self.Arg})'


class PhysicalConnectorInfo(PropertyBasedPMType):
    """PhysicalConnectorInfo defines a number in order to allow to guide the clinical user for a failure,
    e.g., in case of a disconnection of a sensor or an ultrasonic handpiece."""
    # pylint: disable=invalid-name
    ext_Extension = cp.ExtensionNodeProperty()
    Label = cp.SubElementListProperty(pm.Label,
                                      value_class=LocalizedText)  # A human-readable label that describes the physical connector.
    Number = cp.IntegerAttributeProperty('Number')  # Number designates the connector number of the physical connector.
    # pylint: enable=invalid-name
    _props = ['ext_Extension', 'Label', 'Number']

    def __init__(self, labels=None, number=None):
        """
        :param labels: a  list of LocalizedText
        :param number : an integer
        """
        # pylint: disable=invalid-name
        self.Label = labels or []
        self.Number = number
        # pylint: enable=invalid-name

    @classmethod
    def from_node(cls, node):
        obj = cls(None, None)
        cls.Label.update_from_node(obj, node)
        cls.Number.update_from_node(obj, node)
        return obj

    def __repr__(self):
        return f'{self.__class__.__name__}(label={self.Label}, number={self.Number})'


class AlertSignalManifestation(StringEnum):
    AUD = 'Aud'  # Aud = Audible. The ALERT SIGNAL manifests in an audible manner, i.e., the alert can be heard. Example: an alarm sound.
    VIS = 'Vis'  # Vis = Visible. The ALERT SIGNAL manifests in a visible manner, i.e., the alert can be seen. Example: a red flashing light.
    TAN = 'Tan'  # Tan = Tangible. The ALERT SIGNAL manifests in a tangible manner, i.e., the alert can be felt. Example: vibration.
    OTH = 'Oth'  # Oth = Other. The ALERT SIGNAL manifests in a manner not further specified.


class AlertActivation(StringEnum):
    ON = 'On'
    OFF = 'Off'
    PAUSED = 'Psd'


class SystemSignalActivation(PropertyBasedPMType):
    # pylint: disable=invalid-name
    Manifestation = cp.EnumAttributeProperty('Manifestation', default_py_value=AlertSignalManifestation.OTH,
                                             enum_cls=AlertSignalManifestation, is_optional=False)
    State = cp.EnumAttributeProperty('State', default_py_value=AlertActivation.ON,
                                     enum_cls=AlertActivation, is_optional=False)
    # pylint: enable=invalid-name
    _props = ['Manifestation', 'State']

    def __init__(self, manifestation=None, state=None):
        """
        :param manifestation: a pmtypes.AlertSignalManifestation value
        :param state : a pmtypes.AlertActivation value
        """
        # pylint: disable=invalid-name
        self.Manifestation = manifestation
        self.State = state
        # pylint: enable=invalid-name

    @classmethod
    def from_node(cls, node):
        obj = cls(None, None)
        obj.update_from_node(node)
        return obj

    def __repr__(self):
        return f'{self.__class__.__name__}(Manifestation={self.Manifestation}, State={self.State})'


class ProductionSpecification(PropertyBasedPMType):
    # pylint: disable=invalid-name
    SpecType = cp.SubElementProperty(pm.SpecType, value_class=CodedValue)
    ProductionSpec = cp.NodeTextProperty(pm.ProductionSpec)
    ComponentId = cp.SubElementProperty(pm.ComponentId,
                                        value_class=InstanceIdentifier, is_optional=True)
    # pylint: enable=invalid-name
    _props = ['SpecType', 'ProductionSpec', 'ComponentId']

    def __init__(self, spectype=None, productionspec=None, componentid=None):
        """
        :param spectype: a pmtypes.CodedValue value
        :param productionspec: a string
        :param componentid : a pmtypes.InstanceIdentifier value
        """
        # pylint: disable=invalid-name
        self.SpecType = spectype
        self.ProductionSpec = productionspec
        self.ComponentId = componentid
        # pylint: enable=invalid-name

    @classmethod
    def from_node(cls, node):
        obj = cls(None, None)
        obj.update_from_node(node)
        return obj


class BaseDemographics(PropertyBasedPMType):
    # pylint: disable=invalid-name
    Givenname = cp.NodeTextProperty(pm.Givenname, is_optional=True)
    Middlename = cp.SubElementTextListProperty(pm.Middlename)
    Familyname = cp.NodeTextProperty(pm.Familyname, is_optional=True)
    Birthname = cp.NodeTextProperty(pm.Birthname, is_optional=True)
    Title = cp.NodeTextProperty(pm.Title, is_optional=True)
    # pylint: enable=invalid-name
    _props = ('Givenname', 'Middlename', 'Familyname', 'Birthname', 'Title')

    def __init__(self, given_name=None, middle_names=None, family_name=None, birth_name=None, title=None):
        super().__init__()
        # pylint: disable=invalid-name
        self.Givenname = given_name
        self.Middlename = middle_names or []
        self.Familyname = family_name
        self.Birthname = birth_name
        self.Title = title
        # pylint: enable=invalid-name


class PersonReference(PropertyBasedPMType):
    # pylint: disable=invalid-name
    ext_Extension = cp.ExtensionNodeProperty()
    Identification = cp.SubElementListProperty(pm.Identification, value_class=InstanceIdentifier)  # 1...n
    Name = cp.SubElementProperty(pm.Name, value_class=BaseDemographics, is_optional=True)
    # pylint: enable=invalid-name
    _props = ['ext_Extension', 'Identification', 'Name']

    def __init__(self, identifications=None, name=None):
        """
        :param identifications: a list of InstanceIdentifier objects
        :param name: a BaseDemographics object
        """
        # pylint: disable=invalid-name
        if identifications:
            self.Identification = identifications
        self.Name = name
        # pylint: enable=invalid-name


class LocationDetail(PropertyBasedPMType):
    # pylint: disable=invalid-name
    ext_Extension = cp.ExtensionNodeProperty()
    PoC = cp.StringAttributeProperty('PoC')
    Room = cp.StringAttributeProperty('Room')
    Bed = cp.StringAttributeProperty('Bed')
    Facility = cp.StringAttributeProperty('Facility')
    Building = cp.StringAttributeProperty('Building')
    Floor = cp.StringAttributeProperty('Floor')
    # pylint: enable=invalid-name
    _props = ('ext_Extension', 'PoC', 'Room', 'Bed', 'Facility', 'Building', 'Floor')

    def __init__(self, poc=None, room=None, bed=None, facility=None, building=None, floor=None):
        # pylint: disable=invalid-name
        self.PoC = poc
        self.Room = room
        self.Bed = bed
        self.Facility = facility
        self.Building = building
        self.Floor = floor
        # pylint: enable=invalid-name


class LocationReference(PropertyBasedPMType):
    # pylint: disable=invalid-name
    Identification = cp.SubElementListProperty(pm.Identification, value_class=InstanceIdentifier)  # 1...n
    LocationDetail = cp.SubElementProperty(pm.LocationDetail, value_class=LocationDetail)  # optional
    # pylint: enable=invalid-name
    _props = ['Identification', 'LocationDetail']

    def __init__(self, identifications=None, locationdetail=None):
        # pylint: disable=invalid-name
        if identifications:
            self.Identification = identifications
        self.LocationDetail = locationdetail
        # pylint: enable=invalid-name


class PersonParticipation(PersonReference):
    # pylint: disable=invalid-name
    NODETYPE = pm.PersonParticipation
    Role = cp.SubElementListProperty(pm.Role, value_class=CodedValue)  # 0...n
    # pylint: enable=invalid-name
    _props = ['Role', ]

    def __init__(self, identifications=None, name=None, roles=None):
        super().__init__(identifications, name)
        if roles:
            self.Role = roles  # pylint: disable=invalid-name


class ReferenceRange(PropertyBasedPMType):
    """Representation of the normal or abnormal reference range for the measurement"""
    # pylint: disable=invalid-name
    Range = cp.SubElementProperty(pm.Range, value_class=Range)
    Meaning = cp.SubElementProperty(pm.Meaning, value_class=CodedValue, is_optional=True)
    # pylint: enable=invalid-name
    _props = ['Range', 'Meaning']

    def __init__(self, ref_range, meaning=None):
        # pylint: disable=invalid-name
        self.Range = ref_range
        if meaning is not None:
            self.Meaning = meaning
        # pylint: enable=invalid-name


class RelatedMeasurement(PropertyBasedPMType):
    """Related measurements for this clinical observation"""
    # pylint: disable=invalid-name
    Value = cp.SubElementProperty(pm.Value, value_class=Measurement)
    ReferenceRange = cp.SubElementListProperty(pm.ReferenceRange, value_class=ReferenceRange)  # 0...n
    # pylint: enable=invalid-name
    _props = ['Value', 'ReferenceRange']

    def __init__(self, value, reference_range=None):
        # pylint: disable=invalid-name
        self.Value = value
        if reference_range is not None:
            self.ReferenceRange = reference_range
        # pylint: enable=invalid-name


class ClinicalInfo(PropertyBasedPMType):
    # pylint: disable=invalid-name
    Type = cp.SubElementProperty(pm.Type, value_class=CodedValue)  # optional
    Description = cp.SubElementListProperty(pm.Description, value_class=LocalizedText)  # 0...n
    RelatedMeasurement = cp.SubElementListProperty(pm.RelatedMeasurement, value_class=Measurement)  # 0...n
    # pylint: enable=invalid-name
    _props = ['Type', 'Description', 'RelatedMeasurement']

    def __init__(self, typecode=None, descriptions=None, relatedmeasurements=None):
        """
        :param typecode: a CodedValue Instance
        :param descriptions: a list of LocalizedText objects
        :param relatedmeasurements: a list of Measurement objects
        """
        # pylint: disable=invalid-name
        self.Type = typecode
        if descriptions:
            self.Description = descriptions
        if relatedmeasurements:
            self.RelatedMeasurement = relatedmeasurements
        # pylint: enable=invalid-name


class ImagingProcedure(PropertyBasedPMType):
    # pylint: disable=invalid-name
    AccessionIdentifier = cp.SubElementProperty(pm.AccessionIdentifier,
                                                value_class=InstanceIdentifier)  # mandatory
    RequestedProcedureId = cp.SubElementProperty(pm.RequestedProcedureId,
                                                 value_class=InstanceIdentifier)  # mandatory
    StudyInstanceUid = cp.SubElementProperty(pm.StudyInstanceUid, value_class=InstanceIdentifier)  # mandatory
    ScheduledProcedureStepId = cp.SubElementProperty(pm.ScheduledProcedureStepId,
                                                     value_class=InstanceIdentifier)  # mandatory
    Modality = cp.SubElementProperty(pm.Modality, value_class=CodedValue)  # optional
    ProtocolCode = cp.SubElementProperty(pm.ProtocolCode, value_class=CodedValue)  # optional
    # pylint: enable=invalid-name
    _props = ['AccessionIdentifier', 'RequestedProcedureId', 'StudyInstanceUid', 'ScheduledProcedureStepId',
              'Modality', 'ProtocolCode']

    def __init__(self, accessionidentifier, requestedprocedureid, studyinstanceuid, scheduledprocedurestepid,
                 modality=None, protocolcode=None):
        # pylint: disable=invalid-name
        self.AccessionIdentifier = accessionidentifier
        self.RequestedProcedureId = requestedprocedureid
        self.StudyInstanceUid = studyinstanceuid
        self.ScheduledProcedureStepId = scheduledprocedurestepid
        self.Modality = modality
        self.ProtocolCode = protocolcode
        # pylint: enable=invalid-name

    @classmethod
    def from_node(cls, node):
        obj = cls(None, None, None, None)
        obj.update_from_node(node)
        return obj


class OrderDetail(PropertyBasedPMType):
    # pylint: disable=invalid-name
    Start = cp.NodeTextProperty(pm.Start, is_optional=True)  # xsd:dateTime
    End = cp.NodeTextProperty(pm.End, is_optional=True)  # xsd:dateTime
    Performer = cp.SubElementListProperty(pm.Performer, value_class=PersonParticipation)  # 0...n
    Service = cp.SubElementListProperty(pm.Service, value_class=CodedValue)  # 0...n
    ImagingProcedure = cp.SubElementListProperty(pm.ImagingProcedure, value_class=ImagingProcedure)
    # pylint: enable=invalid-name
    _props = ['Start', 'End', 'Performer', 'Service', 'ImagingProcedure']

    def __init__(self, start=None, end=None, performer=None, service=None, imagingprocedure=None):
        """
        :param start: a xsd:DateTime string
        :param end: a xsd:DateTime string
        :param performer: a list of PersonParticipation objects
        :param service: a list of CodedValue objects
        :param imagingprocedure: a list of ImagingProcedure objects
        """
        # pylint: disable=invalid-name
        self.Start = start
        self.End = end
        if performer:
            self.Performer = performer
        if service:
            self.Service = service
        if imagingprocedure:
            self.ImagingProcedure = imagingprocedure
        # pylint: enable=invalid-name


class RequestedOrderDetail(OrderDetail):
    # pylint: disable=invalid-name
    NODETYPE = pm.RequestedOrderDetail
    ReferringPhysician = cp.SubElementProperty(pm.ReferringPhysician, value_class=PersonReference, is_optional=True)
    RequestingPhysician = cp.SubElementProperty(pm.RequestingPhysician, value_class=PersonReference, is_optional=True)
    PlacerOrderNumber = cp.SubElementProperty(pm.PlacerOrderNumber, value_class=InstanceIdentifier)
    # pylint: enable=invalid-name
    _props = ['ReferringPhysician', 'RequestingPhysician', 'PlacerOrderNumber']

    def __init__(self, start=None, end=None, performer=None, service=None, imagingprocedure=None,
                 referringphysician=None, requestingphysician=None, placerordernumber=None):
        """
        :param referringphysician:  a PersonReference
        :param requestingphysician: a PersonReference
        :param placerordernumber:   an InstanceIdentifier
        """
        super().__init__(start, end, performer, service, imagingprocedure)
        # pylint: disable=invalid-name
        self.ReferringPhysician = referringphysician
        self.RequestingPhysician = requestingphysician
        self.PlacerOrderNumber = placerordernumber
        # pylint: enable=invalid-name


class PerformedOrderDetail(OrderDetail):
    # pylint: disable=invalid-name
    NODETYPE = pm.PerformedOrderDetail
    FillerOrderNumber = cp.SubElementProperty(pm.FillerOrderNumber, value_class=InstanceIdentifier, is_optional=True)
    ResultingClinicalInfo = cp.SubElementListProperty(pm.RelevantClinicalInfo, value_class=ClinicalInfo)
    # pylint: enable=invalid-name
    _props = ['FillerOrderNumber', 'ResultingClinicalInfo']

    def __init__(self, start=None, end=None, performer=None, service=None, imagingprocedure=None,
                 fillerordernumber=None, resultingclinicalinfos=None):
        super().__init__(start, end, performer, service, imagingprocedure)
        # pylint: disable=invalid-name
        self.FillerOrderNumber = fillerordernumber
        if resultingclinicalinfos:
            self.ResultingClinicalInfo = resultingclinicalinfos
        # pylint: enable=invalid-name


class WorkflowDetail(PropertyBasedPMType):
    # pylint: disable=invalid-name
    Patient = cp.SubElementProperty(pm.Patient, value_class=PersonReference)
    AssignedLocation = cp.SubElementProperty(pm.AssignedLocation,
                                             value_class=LocationReference, is_optional=True)
    VisitNumber = cp.SubElementProperty(pm.VisitNumber,
                                        value_class=InstanceIdentifier, is_optional=True)
    DangerCode = cp.SubElementListProperty(pm.DangerCode, value_class=CodedValue)
    RelevantClinicalInfo = cp.SubElementListProperty(pm.RelevantClinicalInfo, value_class=ClinicalInfo)
    RequestedOrderDetail = cp.SubElementProperty(pm.RequestedOrderDetail,
                                                 value_class=RequestedOrderDetail, is_optional=True)
    PerformedOrderDetail = cp.SubElementProperty(pm.PerformedOrderDetail,
                                                 value_class=PerformedOrderDetail, is_optional=True)
    # pylint: enable=invalid-name
    _props = ['Patient', 'AssignedLocation', 'VisitNumber', 'DangerCode',
              'RelevantClinicalInfo', 'RequestedOrderDetail', 'PerformedOrderDetail']

    def __init__(self, patient=None, assignedlocation=None, visitnumber=None, dangercode=None,
                 relevantclinicalinfo=None, requestedorderdetail=None, performedorderdetail=None):
        # pylint: disable=invalid-name
        self.Patient = patient
        self.AssignedLocation = assignedlocation
        self.VisitNumber = visitnumber
        if dangercode:
            self.DangerCode = dangercode
        if relevantclinicalinfo:
            self.RelevantClinicalInfo = relevantclinicalinfo
        self.RequestedOrderDetail = requestedorderdetail
        self.PerformedOrderDetail = performedorderdetail
        # pylint: enable=invalid-name


class AbstractMetricDescriptorRelationKindEnum(StringEnum):
    RECOMMENDATION = 'Rcm'
    PRE_SETTING = 'PS'
    SET_OF_SUMMARY_STATISTICS = 'SST'
    EFFECT_ON_CONTAINMENT_TREE_ENTRIES = 'ECE'
    DERIVED_FROM_CONTAINMENT_TREE_ENTRIES = 'DCE'
    OTHER = 'Oth'


class AbstractMetricDescriptorRelation(PropertyBasedPMType):
    """ Relation allows the modelling of relationships between a metric and other containment tree entries.
    """
    # pylint: disable=invalid-name
    Code = cp.SubElementProperty(pm.Code, value_class=CodedValue, is_optional=True)
    Identification = cp.SubElementProperty(pm.Identification, value_class=InstanceIdentifier,
                                           is_optional=True)
    Kind = cp.EnumAttributeProperty('Kind', enum_cls=AbstractMetricDescriptorRelationKindEnum, is_optional=False)
    Entries = cp.EntryRefListAttributeProperty('Entries')
    # pylint: enable=invalid-name
    _props = ['Code', 'Identification', 'Kind', 'Entries']

    def __init__(self):
        pass


Relation = AbstractMetricDescriptorRelation


class PatientType(StringEnum):
    UNSPECIFIED = 'Unspec'
    ADULT = 'Ad'
    ADOLESCENT = 'Ado'
    PEDIATRIC = 'Ped'
    INFANT = 'Inf'
    NEONATAL = 'Neo'
    OTHER = 'Oth'


class T_Sex(StringEnum):  # pylint: disable=invalid-name
    UNSPEC = 'Unspec'
    MALE = 'M'
    FEMALE = 'F'
    UNKNOWN = 'Unkn'


class PatientDemographicsCoreData(BaseDemographics):
    # pylint: disable=invalid-name
    NODETYPE = pm.PatientDemographicsCoreData
    Sex = cp.NodeEnumTextProperty(T_Sex, pm.Sex, is_optional=True)
    PatientType = cp.NodeEnumTextProperty(PatientType, pm.PatientType, is_optional=True)
    DateOfBirth = cp.DateOfBirthProperty(pm.DateOfBirth, is_optional=True)
    Height = cp.SubElementProperty(pm.Height, value_class=Measurement, is_optional=True)
    Weight = cp.SubElementProperty(pm.Weight, value_class=Measurement, is_optional=True)
    Race = cp.SubElementProperty(pm.Race, value_class=CodedValue, is_optional=True)
    # pylint: enable=invalid-name
    _props = ('Sex', 'PatientType', 'DateOfBirth', 'Height', 'Weight', 'Race')

    def set_birthdate(self, date_time_of_birth_string):
        """ this method accepts a string, format acc. to XML Schema: xsd:dateTime, xsd:date, xsd:gYearMonth or xsd:gYear
        Internally it holds it as a datetime object, so specific formatting of the dateTimeOfBirth_string will be lost."""
        # pylint: disable=invalid-name
        if not date_time_of_birth_string:
            self.DateOfBirth = None
        else:
            self.DateOfBirth = cp.DateOfBirthProperty.mk_value_object(date_time_of_birth_string)
        # pylint: enable=invalid-name


class NeonatalPatientDemographicsCoreData(PatientDemographicsCoreData):
    # pylint: disable=invalid-name
    NODETYPE = pm.NeonatalPatientDemographicsCoreData
    GestationalAge = cp.SubElementProperty(pm.GestationalAge, value_class=Measurement,
                                           is_optional=True)
    BirthLength = cp.SubElementProperty(pm.BirthLength, value_class=Measurement)
    BirthWeight = cp.SubElementProperty(pm.BirthWeight, value_class=Measurement)
    HeadCircumference = cp.SubElementProperty(pm.HeadCircumference, value_class=Measurement)
    Mother = cp.SubElementProperty(pm.Mother, value_class=PersonReference)
    # pylint: enable=invalid-name
    _props = ('GestationalAge', 'BirthLength', 'BirthWeight', 'HeadCircumference', 'Mother')


class T_Udi(PropertyBasedPMType):
    """Part of Meta data"""
    # pylint: disable=invalid-name
    DeviceIdentifier = cp.NodeTextProperty(pm.DeviceIdentifier)
    HumanReadableForm = cp.NodeTextProperty(pm.HumanReadableForm)
    Issuer = cp.SubElementProperty(pm.Issuer, value_class=InstanceIdentifier)
    Jurisdiction = cp.SubElementProperty(pm.Jurisdiction,
                                         value_class=InstanceIdentifier, is_optional=True)
    # pylint: enable=invalid-name
    _props = ['DeviceIdentifier', 'HumanReadableForm', 'Issuer', 'Jurisdiction']

    def __init__(self, device_identifier=None, humanreadable_form=None, issuer=None, jurisdiction=None):
        """
        UDI fragments as defined by the FDA. (Only used in BICEPS Final)
        :param device_identifier: a string
        :param humanreadable_form: a string
        :param issuer: an InstanceIdentifier
        :param jurisdiction: an InstanceIdentifier (optional)
        """
        # pylint: disable=invalid-name
        self.DeviceIdentifier = device_identifier
        self.HumanReadableForm = humanreadable_form
        self.Issuer = issuer
        self.Jurisdiction = jurisdiction
        # pylint: enable=invalid-name


class MetaData(PropertyBasedPMType):
    # pylint: disable=invalid-name
    Udi = cp.SubElementListProperty(pm.Udi, value_class=T_Udi)
    LotNumber = cp.NodeTextProperty(pm.LotNumber, is_optional=True)
    Manufacturer = cp.SubElementListProperty(pm.Manufacturer, value_class=LocalizedText)
    ManufactureDate = cp.NodeTextProperty(pm.ManufactureDate, is_optional=True)
    ExpirationDate = cp.NodeTextProperty(pm.ExpirationDate, is_optional=True)
    ModelName = cp.SubElementListProperty(pm.ModelName, value_class=LocalizedText)
    ModelNumber = cp.NodeTextProperty(pm.ModelNumber, is_optional=True)
    SerialNumber = cp.SubElementTextListProperty(pm.SerialNumber)
    # pylint: enable=invalid-name
    _props = ['Udi', 'LotNumber', 'Manufacturer', 'ManufactureDate', 'ExpirationDate',
              'ModelName', 'ModelNumber', 'SerialNumber']

    def __init__(self):
        pass


class T_CalibrationResult(PropertyBasedPMType):
    # pylint: disable=invalid-name
    Code = cp.SubElementProperty(pm.Code, value_class=CodedValue)
    Value = cp.SubElementProperty(pm.Value, value_class=Measurement)
    # pylint: enable=invalid-name
    _props = ['Code', 'Value']


class T_CalibrationDocumentation(PropertyBasedPMType):
    # pylint: disable=invalid-name
    Documentation = cp.SubElementListProperty(pm.Documentation, value_class=LocalizedText)
    CalibrationResult = cp.SubElementListProperty(pm.CalibrationResult, value_class=T_CalibrationResult)
    # pylint: enable=invalid-name
    _props = ['Documentation', 'CalibrationResult']


class T_CalibrationState(StringEnum):  # only used in CalibrationInfo
    NOT_CALIBRATED = 'No'
    CALIBRATION_REQUIRED = 'Req'
    RUNNING = 'Run'
    CALIBRATED = 'Cal'
    OTHER = 'Oth'


class T_CalibrationType(StringEnum):  # only used in CalibrationInfo
    OFFSET = 'Offset'
    GAIN = 'Gain'
    TWO_POINT_CALIBRATION = 'TP'
    UNSPEC = 'Unspec'


class CalibrationInfo(PropertyBasedPMType):
    # pylint: disable=invalid-name
    CalibrationDocumentation = cp.SubElementListProperty(pm.CalibrationDocumentation,
                                                         value_class=T_CalibrationDocumentation)
    ComponentCalibrationState = cp.EnumAttributeProperty('ComponentCalibrationState',
                                                         enum_cls=T_CalibrationState)
    CalibrationType = cp.EnumAttributeProperty('CalibrationType',
                                               implied_py_value=T_CalibrationType.UNSPEC,
                                               enum_cls=T_CalibrationType)
    Time = cp.TimestampAttributeProperty('Time')
    # pylint: enable=invalid-name
    _props = ['CalibrationDocumentation', 'ComponentCalibrationState', 'CalibrationType', 'Time']


class ApprovedJurisdictions(PropertyBasedPMType):
    # pylint: disable=invalid-name
    ApprovedJurisdiction = cp.SubElementListProperty(pm.ApprovedJurisdiction,
                                                     value_class=InstanceIdentifier)
    _props = ['ApprovedJurisdiction']


###################################################################################
# following : classes that serve only as name spaces

class SafetyClassification(StringEnum):
    INF = 'Inf'
    MED_A = 'MedA'
    MED_B = 'MedB'
    MED_C = 'MedC'


class MdsOperatingMode(StringEnum):
    NORMAL = 'Nml'
    DEMO = 'Dmo'
    SERVICE = 'Srv'
    MAINTENANCE = 'Mtn'


class ComponentActivation(StringEnum):
    ON = 'On'
    NOT_READY = 'NotRdy'
    STANDBY = 'StndBy'
    OFF = 'Off'
    SHUTDOWN = 'Shtdn'
    FAILURE = 'Fail'


class ContextAssociation(StringEnum):
    NO_ASSOCIATION = 'No'
    PRE_ASSOCIATION = 'Pre'
    ASSOCIATED = 'Assoc'
    DISASSOCIATED = 'Dis'


class AlertConditionMonitoredLimits(StringEnum):
    ALL_ON = 'All'
    LOW_OFF = 'LoOff'
    HIGH_OFF = 'HiOff'
    ALL_OFF = 'None'


class AlertConditionPriority(StringEnum):
    NONE = 'None'
    LOW = 'Lo'
    MEDIUM = 'Me'
    HIGH = 'Hi'


class AlertConditionKind(StringEnum):
    PHYSIOLOGICAL = 'Phy'
    TECHNICAL = 'Tec'
    OTHER = 'Oth'


class CanEscalateAlertConditionPriority(StringEnum):
    LOW = 'Lo'
    MEDIUM = 'Me'
    HIGH = 'Hi'


class CanDeEscalateAlertConditionPriority(StringEnum):
    MEDIUM = 'Me'
    LOW = 'Lo'
    NONE = 'None'


class AlertSignalPresence(StringEnum):
    ON = 'On'
    OFF = 'Off'
    LATCH = 'Latch'
    ACK = 'Ack'


class MetricAvailability(StringEnum):
    INTERMITTENT = 'Intr'
    CONTINUOUS = 'Cont'


class MetricCategory(StringEnum):
    UNSPECIFIED = 'Unspec'
    MEASUREMENT = 'Msrmt'
    CALCULATION = 'Clc'
    SETTING = 'Set'
    PRESETTING = 'Preset'
    RECOMMENDATION = 'Rcmm'


class InvocationState(StringEnum):  # a namespace class
    WAIT = 'Wait'  # Wait = Waiting. The operation has been queued and waits for execution.
    START = 'Start'  # Start = Started. The execution of the operation has been started
    CANCELLED = 'Cnclld'  # Cnclld = Cancelled. The execution has been cancelled by the SERVICE PROVIDER.
    CANCELLED_MANUALLY = 'CnclldMan'  # CnclldMan = Cancelled Manually. The execution has been cancelled by the operator.
    FINISHED = 'Fin'  # Fin = Finished. The execution has been finished.
    FINISHED_MOD = 'FinMod'  # FinMod = Finished with modification. As the requested target value could not be reached, the next best value has been chosen and used as target value.
    FAILED = 'Fail'  # The execution has been failed.


class InvocationError(StringEnum):
    UNSPECIFIED = 'Unspec'  # An unspecified error has occurred. No more information about the error is available.
    UNKNOWN_OPERATION = 'Unkn'  # Unknown Operation. The HANDLE to the operation object is not known.
    INVALID_VALUE = 'Inv'  # Invalid Value. The HANDLE to the operation object does not match the invocation request message
    OTHER = 'Oth'  # Another type of error has occurred. More information on the error MAY be available.


class Units:
    UnitLess = CodedValue('262656')  # used if a metric has no unit


class DescriptionModificationTypes(StringEnum):
    CREATE = 'Crt'
    UPDATE = 'Upt'
    DELETE = 'Del'


class DerivationMethod(StringEnum):
    AUTOMATIC = 'Auto'
    MANUAL = 'Man'


class T_AccessLevel(StringEnum):  # pylint: disable=invalid-name
    USER = 'Usr'
    CLINICAL_SUPER_USER = 'CSUsr'
    RESPONSIBLE_ORGANIZATION = 'RO'
    SERVICE_PERSONNEL = 'SP'
    OTHER = 'Oth'


class AlertSignalPrimaryLocation(StringEnum):
    LOCAL = 'Loc'
    REMOTE = 'Rem'


# mapping of types: xsi:type information to classes
# find all classes in this module that have a member "NODETYPE"
classes = inspect.getmembers(sys.modules[__name__],
                             lambda member: inspect.isclass(member) and member.__module__ == __name__)
classes_with_NODETYPE = [c[1] for c in classes if hasattr(c[1], 'NODETYPE') and c[1].NODETYPE is not None]
# make a dictionary from found classes: (Key is NODETYPE, value is the class itself
# _state_lookup_by_type = dict([(c.NODETYPE, c) for c in classes_with_NODETYPE])
_name_class_lookup = {c.NODETYPE: c for c in classes_with_NODETYPE}


def _get_pmtypes_class(qname: etree_.QName):
    """
    :param qname: a QName instance
    """
    # return _name_class_lookup.get(qname)
    try:
        return _name_class_lookup[qname]
    except KeyError:
        raise KeyError(f'{qname.namespace}.{qname.localname}')
