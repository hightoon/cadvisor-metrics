# cAdvisor Metrics

This project addresses the problem of collecting [Docker](https://github.com/docker/docker) container metrics from [cadvisor](https://github.com/google/cadvisor) at scale. 
If you are not using Kubernetes or CoreOS you can't (as of today) take advantage of the [Heapster](https://github.com/GoogleCloudPlatform/heapster) project, which solves essentially the same problem. 
The work in this repo attempts to fill the metrics collection gap for those running Docker outside of the Kubernetes or CoreOS ecosystems. 
Another approach is to use cadvisor's [InfluxDB](https://github.com/influxdb/influxdb) integration, however this project can be viewed as an alternative, or additional, means of storing cadvisor metrics. 

At Catalyze, our [Platform as a Service](https://catalyze.io/paas) uses Docker containers to host customer applications
and associated infrastructure (databases, logging, etc). A single customer environment will span multiple hosts and therefore
we need a mechanism for centralized metrics collection and aggregation.

The approach used here is to collect metrics at the container level using cadvisor and then send them back to a centralized collection point.
Currently cadvisor returns the last minute's worth of metrics by default, in 1 second intervals. The sender process
runs every minute (via cron) and then rolls up the 1 second intervals into a 1 minute interval. The collector will put the result
into [Redis](https://github.com/antirez/redis) and trim off the oldest entry in order to maintain a sliding window.

The metrics stored in Redis are transient in that only the last 1440 entries (1 day, configurable) are stored. 
Entries should be rolled off into some other application or collection tool for long term storage and analysis.

## Collector

The collector is a Falcon API endpoint, currently a single POST route that accepts metrics from senders running on
any number of hosts. The collector receives Docket container metrics from hosts running the sender (see below) and 
stores them in Redis. 

The following section explains how to run the container via Docker.
If you are not interested in running the collector this way you can run it via python. 
See [the source](collector/collector.py) for instructions.

### Running with Docker:

Start by building the collector image (these steps assume the repo is cloned and is the working directory):

    cd collector/
    sudo docker build -t collector .

To run the collector, pass in the port to use in COLLECTOR_PORT and expose that port. 
You will also need to provide the Redis IP and port (defaults to `redis.local` and `6379` respectively). 

    sudo docker run \
      -e 'COLLECTOR_REDIS_HOST=192.168.222.5' \
      -e 'COLLECTOR_REDIS_PORT=6379' \
      -e 'COLLECTOR_PORT=8787' \
      --restart on-failure:5 \
      --name collector \
      -p 8787:8787 -d -t collector
      

## Sender

The sender is a Docker container that runs a python script (sender.py) every minute.
It polls cadvisor and forwards the results to the collector. 
It should be deployed on every host where container metrics are to be collected. 

### Running cadvisor

The sender polls cadvisor every minute via cadvisor's REST API. 
In these instructions we expose cadvisor on port 8989.
The currently supported cadvisor version is 0.9.0.
See the [cadvisor repo](https://github.com/google/cadvisor) for more information.

    sudo docker run \
      --volume=/:/rootfs:ro \
      --volume=/var/run:/var/run:rw \
      --volume=/sys:/sys:ro \
      --volume=/var/lib/docker/:/var/lib/docker:ro \
      --publish=8989:8080 \
      --detach=true \
      --restart on-failure:5 \
      --name=cadvisor \
      google/cadvisor:0.9.0

### Running the sender

The sender needs to know where to get metrics from cadvisor and where to send them (the collector's IP and port).

First build the image:

    cd ../sender/
    sudo docker build -t sender .

Start the container: 

    sudo docker run \
      -e 'COLLECTOR_URL=http://192.168.222.5:8787/cadvisor/metrics/' \
      -e 'CADVISOR_URL=http://192.168.222.6:8989/api/v1.2' \
      --restart on-failure:5 \
      --name sender \
      -d sender

## Accessing Metrics

By default the collector will keep the last 1440 minutes of data (one days' worth) in Redis.
Each container will have 1440 1-minute metrics.
The results are stored as Redis [lists](http://redis.io/commands#list) with keys of the form `stat:<container-name>`.
Below are some examples of how to access various metrics and data items via `redis-cli`.

Get a list of containers:

    smembers names

Get container metadata (e.g. IP address):

    get name:<container-name>

Determine the number of entries for a container:

    llen stats:<container-name>

Retrieve the 1st entry:

    lrange stats:<container-name> 0 0

Retrieve all entries:

    lrange stats:<container-name> 0 -1

Get the machine info for an IP (stores the /machine route data from cadvisor):

    get ip:<host-ip>
    
## Metrics JSON Format

Data is stored as JSON in Redis. The top level keys are as follows:
 
* **name**: the name of the container, a string
* **ts**: the starting minute of the entry in epoch time (seconds)
* **network**: stats for network bytes and packets in an out of the container
* **diskio**: disk I/O stats for the container
* **memory**: memory usage in KB
* **cpu**: cpu usage from the start of the minute to the end

### Network data:
* Each **network** section contains **tx_bytes**, **rx_bytes**, **tx_packets** and **rx_packets**
* Each sub-section contains **ave** (average), **min** (minimum) and **max** (maximum) values

### Disk I/O data:
* Each **diskio** section contains **read**, **write**, **async** and **sync**
* The I/O values are computed as the delta of the value from the beginning of the minute to the end of the minute
* The values for **read** and **write** are in KB
* The values for **sync** and **async** are in number of operations
* The total bytes is equal to the sum of reads and writes
* The total number of I/O operations is equal to the sum of async and sync

### Memory data:
* The **memory** section contains **ave** (average), **min** (minimum) and **max** (maximum) values
* All values are in KB

### CPU data:
* The **usage** value is the delta of cumulative CPU usage from the beginning of the minute to the end of the minute. 
* The **load** section seems to always report 0. This is considered an outstanding bug to be addressed in a future release. 

Here is an example entry:
 
```
{
  "name": "471c2fd8-674c-4397-a660-e80338e01269",
  "ts": 1427762581,
  "network": {
    "tx_bytes": {
      "ave": 235189226,
      "min": 235174806,
      "max": 235203379
    },
    "rx_packets": {
      "ave": 899691,
      "min": 899603,
      "max": 899776
    },
    "tx_packets": {
      "ave": 1214692,
      "min": 1214571,
      "max": 1214811
    },
    "rx_bytes": {
      "ave": 2885699730,
      "min": 2885390818,
      "max": 2886008373
    }
  },
  "diskio": {
    "read": 0,
    "async": 0,
    "write": 0,
    "sync": 0
  },
  "memory": {
    "ave": 252555,
    "min": 252176,
    "max": 252868
  },
  "cpu": {
    "usage": 216588252,
    "load": {
      "ave": 0,
      "min": 0,
      "max": 0
    }
  }
}
```

## Post-processing

We run a number of scripts against the collected metrics to monitor customer applications and gain insights into potential load problems.
For this initial release we are providing an [example script](scripts/stats_by_ip.py) that aggregates metrics on a per-IP basis. 
This script is best run periodically via cron and then analyzed with something like [pandas](http://pandas.pydata.org/).

Run the script after adjusting the Redis host/port as needed and record the output:

    python stats_by_ip.py > stats.csv

Here is some example code suitable for an [ipython notebook](http://ipython.org/notebook.html) for plotting memory usage by IP: 

```
%matplotlib inline

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
pd.set_option('max_columns', 50)


stats = pd.read_csv('stats.csv')
by_ip = stats.groupby('ip')
by_ip['memory'].mean().plot(kind='bar')
```

# Contributing

If you find something wrong with our code please submit a GitHub issue or, better yet, submit a pull request. 
For other inquiries please email support@catalyze.io.   

