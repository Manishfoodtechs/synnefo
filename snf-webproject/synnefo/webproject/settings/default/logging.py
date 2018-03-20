# -*- coding: utf-8 -*-
#
# Logging configuration
##################################


FORMATTERS = {
    'simple': {
        'format': '%(asctime)s [%(levelname)s] %(message)s'
    },
    'verbose': {
        'format': '%(asctime)s [%(process)d] %(name)s %(module)s [%(levelname)s] %(message)s'
    },
    'django': {
        'format': '[%(asctime)s] %(levelname)s %(message)s',
        'datefmt': '%d/%b/%Y %H:%M:%S'
    },
}


LOGGING_SETUP = {
    'version': 1,
    'disable_existing_loggers': False,

    'formatters':  FORMATTERS,
    'handlers': {
        'null': {
            'class': 'logging.NullHandler',
        },
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose'
        },
        'file': {
            'class': 'logging.handlers.WatchedFileHandler',
            'filename': '/var/log/synnefo/synnefo.log',
            'formatter': 'verbose'
        },
        'syslog': {
            'class': 'logging.handlers.SysLogHandler',
            'address': '/dev/log',
            # 'address': ('localhost', 514),
            'facility': 'daemon',
            'formatter': 'verbose',
            'level': 'INFO',
        },
        'mail_admins': {
            'level': 'ERROR',
            'class': 'django.utils.log.AdminEmailHandler',
            'include_html': False,
        }
    },

    'loggers': {
        '': {
            'handlers': ['file'],
            'level': 'INFO'
        },
        'django.request': {
            'handlers': ['mail_admins'],
            'level': 'ERROR',
            'propagate': True,
        },
        'synnefo': {
            'handlers': ['file'],
            'level': 'INFO',
            'propagate': 0
        },
    }
}

#LOGGING_SETUP['loggers']['synnefo.admin'] = {'level': 'INFO', 'propagate': 1}
#LOGGING_SETUP['loggers']['synnefo.api'] = {'level': 'INFO', 'propagate': 1}
#LOGGING_SETUP['loggers']['synnefo.db'] = {'level': 'INFO', 'propagate': 1}
#LOGGING_SETUP['loggers']['synnefo.logic'] = {'level': 'INFO', 'propagate': 1}

# To set logging level for plankton to DEBUG just uncomment the follow line:
# LOGGING_SETUP['loggers']['synnefo.plankton'] = {'level': 'INFO', 'propagate': 1}

SNF_MANAGE_LOGGING_SETUP = {
    'version': 1,
    'disable_existing_loggers': False,

    'filters': {
        'suppress_deprecated': {
            '()': 'synnefo.webproject.logging_filter.SuppressDeprecated'
        }
    },

    'formatters': FORMATTERS,

    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
            'filters': ['suppress_deprecated'],
            'level': 'WARNING',
        },
        'file': {
            'class': 'logging.handlers.WatchedFileHandler',
            'filename': '/var/log/synnefo/synnefo.log',
            'formatter': 'verbose'
        },
    },

    'loggers': {
        '': {
            'handlers': ['file', 'console'],
            'level': 'INFO',
        },
    }
}
