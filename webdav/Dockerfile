FROM golang:1.26-alpine3.23 AS build

ARG VERSION="untracked"
ARG GOPROXY=https://goproxy.cn,direct
ARG GOSUMDB=sum.golang.org

WORKDIR /webdav/

ENV GO111MODULE=on
ENV GOPROXY=${GOPROXY}
ENV GOSUMDB=${GOSUMDB}

COPY ./go.mod ./
COPY ./go.sum ./
RUN go mod download

COPY . /webdav/
RUN go build -o main -trimpath -ldflags="-s -w -X 'github.com/hacdias/webdav/v5/cmd.version=$VERSION'" .

FROM scratch

COPY --from=build /webdav/main /bin/webdav

EXPOSE 6065

ENTRYPOINT [ "webdav" ]
