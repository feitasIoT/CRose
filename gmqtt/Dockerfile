FROM golang:alpine3.23 AS builder

RUN apk add --no-cache make git ca-certificates

ADD . /go/src/github.com/DrmagicE/gmqtt
WORKDIR /go/src/github.com/DrmagicE/gmqtt

ENV GO111MODULE=on
ENV GOPROXY=https://goproxy.cn,direct
ENV GOSUMDB=off

EXPOSE 1883 8883 8082 8083 8084

RUN make binary

FROM alpine:3.23

WORKDIR /gmqttd
COPY --from=builder /go/src/github.com/DrmagicE/gmqtt/build/gmqttd .
RUN mkdir /etc/gmqtt
COPY ./cmd/gmqttd/default_config.yml /etc/gmqtt/gmqttd.yml
ENV PATH=$PATH:/gmqttd
RUN chmod +x gmqttd
ENTRYPOINT ["gmqttd","start"]


