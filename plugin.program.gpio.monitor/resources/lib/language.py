from slyguy.language import BaseLanguage


class Language(BaseLanguage):
    ADD_BTN                 = 30000
    RELOAD_SERVICE          = 30001
    GPIOZERO_ERROR          = 30002
    PIN_LABEL               = 30003
    INSTALL_SERVICE         = 30004
    RELOAD_SERVICE_DESC     = 30005
    INSTALL_SERVICE_DESC    = 30006
    DELETE_BTN              = 30007
    DISABLE_OTHER_BTN       = 30008
    CONFIRM_DELETE_BTN      = 30009
    AUTO_RELOAD_SERVICE     = 30010
    BTN_LABEL               = 30011
    SERVICE_RELOADED        = 30012
    BTN_PIN                 = 30013
    BTN_NAME                = 30014
    BTN_ENABLED             = 30015
    BTN_PULLUP              = 30016
    BTN_BOUNCE_TIME         = 30017
    BTN_HOLD_TIME           = 30018
    BTN_HOLD_REPEAT         = 30019
    BTN_WHEN_PRESSED        = 30020
    BTN_WHEN_RELEASED       = 30021
    BTN_WHEN_HELD           = 30022
    BTN_OPTION              = 30023
    BTN_PIN_DESC            = 30024
    BTN_NAME_DESC           = 30025
    BTN_ENABLED_DESC        = 30026
    BTN_PULLUP_DESC         = 30027
    BTN_BOUNCE_TIME_DESC    = 30028
    BTN_HOLD_TIME_DESC      = 30029
    BTN_HOLD_REPEAT_DESC    = 30030
    BTN_WHEN_PRESSED_DESC   = 30031
    BTN_WHEN_RELEASED_DESC  = 30032
    BTN_WHEN_HELD_DESC      = 30033
    ADD_BTN_DESC            = 30034
    SYSTEM_UNSUPPORTED      = 30035
    SERVICE_NOT_INSTALLED   = 30036
    STATUS_INACTIVE         = 30037
    STATUS_ACTIVE           = 30038
    STATUS_ERROR            = 30039
    STATUS_DISABLED         = 30040
    BTN_STATUS              = 30041
    BTN_ERROR               = 30042
    RESTART_REQUIRED        = 30043
    XBIAN_PASSWORD          = 30044
    XBIAN_ERROR             = 30045
    STATUS_ACTIVE_DESC      = 30046
    STATUS_INACTIVE_DESC    = 30047
    STATUS_DISABLED_DESC    = 30048
    STATUS_ERROR_DESC       = 30049
    TEST_PRESS              = 30050
    TEST_RELEASE            = 30051
    TEST_HOLD               = 30052
    SIMULATION              = 30053


_ = Language()
