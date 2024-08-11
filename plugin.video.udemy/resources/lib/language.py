from slyguy.language import BaseLanguage


class Language(BaseLanguage):
    MY_COURSES       = 30000
    LOGIN_ERROR      = 30001
    COURSE_INFO      = 30002
    SECTION_LABEL    = 30003
    NO_STREAM_ERROR  = 30004
    VMP_WARNING      = 30005
    BUSINESS_ACCOUNT = 30006
    BUSINESS_NAME    = 30007


_ = Language()
