#!/bin/sh
set -eu
case " ${RENEWED_DOMAINS:-} " in
    *" dozzle.chuda.cc "*|*" grafana.chuda.cc "*)
        /usr/sbin/nginx -t
        /usr/bin/systemctl reload nginx
        ;;
esac
