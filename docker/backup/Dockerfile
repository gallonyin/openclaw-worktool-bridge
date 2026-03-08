FROM alpine:3.20

RUN apk add --no-cache sqlite busybox-suid tzdata

WORKDIR /backup
COPY docker/backup/backup.sh /usr/local/bin/backup.sh
COPY docker/backup/crontab /etc/crontabs/root
RUN chmod +x /usr/local/bin/backup.sh

CMD ["crond", "-f", "-l", "2"]
