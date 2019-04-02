"""
Copyright 2019 Goldman Sachs.
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
"""
from concurrent.futures import Future
import datetime as dt
import dateutil
from enum import EnumMeta
from inspect import signature, Parameter
import logging
import pandas as pd
from typing import Union, get_type_hints

_logger = logging.getLogger(__name__)


class EnumBase:
    pass


class Base:

    """The base class for all generated classes"""

    def __init__(self):
        self.__calced_hash = None

    def _property_changed(self, prop):
        self.__calced_hash = None

    def __hash__(self):
        if self.__calced_hash is None:
            properties = (i for i in dir(self.__class__) if isinstance(getattr(self.__class__, i), property))
            prop = next(properties, None)
            self.__calced_hash = hash(getattr(self, prop)) if prop else 1
            for prop in properties:
                self.__calced_hash ^= hash(getattr(self, prop))

        return self.__calced_hash

    def __eq__(self, other):
        properties = (i for i in dir(self.__class__) if isinstance(getattr(self.__class__, i), property))
        return\
            type(self) == type(other) and\
            (self.__calced_hash is None or other.__calced_hash is None or self.__calced_hash == other.__calced_hash) and\
            all(getattr(self, p) == getattr(other, p) for p in properties)

    def __ne__(self, other):
        return not self.__eq__(other)

    @classmethod
    def properties(cls) -> set:
        """The public property names of this class"""
        return set(i for i in dir(cls) if isinstance(getattr(cls, i), property))

    def as_dict(self) -> dict:
        """Dictionary of the public, non-null properties and values"""
        properties = self.properties()
        values = [getattr(self, p) for p in properties]
        return dict((p, v) for p, v in zip(properties, values) if v is not None)

    def __from_dict(self, values: dict):
        for prop in self.properties():
            if prop in values:
                prop_value = values[prop]
                return_hints = get_type_hints(getattr(self.__class__, prop).fget).get('return')
                if hasattr(return_hints, '__origin__'):
                    prop_type = return_hints.__origin__
                else:
                    prop_type = return_hints

                if prop_type == Union:
                    prop_type = next((a for a in return_hints.__args__ if issubclass(a, (Base, EnumBase))), None)

                if issubclass(prop_type, dt.datetime):
                    setattr(self, prop, dateutil.parser.isoparse(prop_value))
                elif issubclass(prop_type, dt.date):
                    setattr(self, prop, dateutil.parser.isoparse(prop_value).date())
                elif issubclass(prop_type, EnumBase):
                    setattr(self, prop, get_enum_value(prop_type, prop_value))
                elif issubclass(prop_type, Base):
                    setattr(self, prop, prop_type.from_dict(prop_value))
                elif issubclass(prop_type, (list, tuple)):
                    item_type = return_hints.__args__[0]
                    if issubclass(item_type, (Base, EnumBase)):
                        item_values = tuple(item_type.from_dict(v) for v in prop_value)
                    else:
                        item_values = tuple(prop_value)
                    setattr(self, prop, item_values)
                else:
                    setattr(self, prop, prop_value)

    @classmethod
    def from_dict(cls, values: dict) -> 'Base':
        """
        Construct an instance of this type from a dictionary

        :param values: a dictionary (potentitally nested)
        :return: an instance of this type, populated with values
        """
        args = [k for k, v in signature(cls.__init__).parameters.items() if v.default == Parameter.empty][1:]
        required = {a: values.get(a) for a in args}
        instance = cls(**required)
        instance.__from_dict(values)
        return instance


class Priceable(Base):

    """A priceable, such as a derivative instrument"""

    PROVIDER = None

    def get_quantity(self) -> float:
        """
        Quantity of the instrument
        """
        return 1

    def provider(self) -> 'RiskApi':
        """
        The risk provider - defaults to GsRiskApi
        """
        if self.PROVIDER is None:
            from gs_quant.api.gs.risk import GsRiskApi
            self.PROVIDER = GsRiskApi

        return self.PROVIDER

    def resolve(self):
        """
        Resolve non-supplied properties of an instrument

        **Examples**

        >>> from gs_quant.instrument import IRSwap
        >>>
        >>> swap = IRSwap('Pay', '10y', 'USD')
        >>> rate = swap.fixedRate

        rate is None

        >>> swap.resolve()
        >>> rate = swap.fixedRate

        rates is now the solved fixed rate
        """
        from gs_quant.risk import PricingContext
        PricingContext.current.resolve_fields(self)

    def dollar_price(self) -> Union[float, Future]:
        """
        Present value in USD

        :return:  a float or a future, depending on whether the current PricingContext is async, or has been entered

        **Examples**

        >>> from gs_quant.instrument import IRCap
        >>>
        >>> cap = IRCap('1y', 'EUR')
        >>> price = cap.dollar_price()

        price is the present value in USD (a float)

        >>> cap_usd = IRCap('1y', 'USD')
        >>> cap_eur = IRCap('1y', 'EUR')
        >>>
        >>> from gs_quant.risk import PricingContext
        >>>
        >>> with PricingContext():
        >>>     price_usd_f = cap_usd.dollar_price()
        >>>     price_eur_f = cap_eur.dollar_price()
        >>>
        >>> price_usd = price_usd_f.result()
        >>> price_eur = price_eur_f.result()

        price_usd_f and price_eur_f are futures, price_usd and price_eur are floats
        """

        from gs_quant.risk import DollarPrice
        return self.calc(DollarPrice)

    def price(self) -> Union[float, Future]:
        """
        Present value in local currency. Note that this is not yet supported on all instruments

        ***Examples**

        >>> from gs_quant.instrument import IRSwap
        >>>
        >>> swap = IRSwap('Pay', '10y', 'EUR')
        >>> price = swap.price()

        price is the present value in EUR (a float)
        """
        from gs_quant.risk import Price
        return self.calc(Price)

    def calc(self, risk_measure: 'RiskMeasure') -> Union[float, pd.DataFrame, Future]:
        """
        Calculate the value of the risk_measure

        :param risk_measure: the risk measure to compute, e.g. IRDelta (from gs_quant.risk)
        :return: a float or dataframe, depending on whether the value is scalar or structured, or a future thereof (depending on how PricingContext is being used)

        **Examples**

        >>> from gs_quant.instrument import IRCap
        >>> from gs_quant.risk import IRDelta
        >>>
        >>> cap = IRCap('1y', 'USD')
        >>> delta = cap.calc(IRDelta)

        delta is a dataframe

        >>> from gs_quant.instrument import EqOption
        >>> from gs_quant.risk import EqDelta
        >>>
        >>> option = EqOption('.SPX', '3m', 'ATMF', 'Call', 'European')
        >>> delta = option.calc(EqDelta)

        delta is a float

        >>> from gs_quant.risk import PricingContext
        >>>
        >>> cap_usd = IRCap('1y', 'USD')
        >>> cap_eur = IRCap('1y', 'EUR')

        >>> with PricingContext():
        >>>     usd_delta_f = cap_usd.calc(IRDelta)
        >>>     eur_delta_f = cap_eur.calc(IRDelta)
        >>>
        >>> usd_delta = usd_delta_f.result()
        >>> eur_delta = eur_delta_f.result()

        usd_delta_f and eur_delta_f are futures, usd_delta and eur_delta are dataframes
        """
        from gs_quant.risk import PricingContext, get_specific_risk_measure
        specific_risk_measure = get_specific_risk_measure(risk_measure, self)
        if specific_risk_measure is None:
            raise ValueError('Unsupported risk measure')

        return PricingContext.current.calc(self, specific_risk_measure)


class Instrument(Priceable):
    pass


def get_enum_value(enum_type: EnumMeta, value: str):
    if value in (None, 'None'):
        return None

    enum_value = next((i for i in enum_type if i.value == value), None)
    if enum_value is None:
        _logger.warning('Setting value to {}, which is not a valid entry in {}'.format(value, enum_type))
        enum_value = value

    return enum_value