from __future__ import print_function

"""
This script is intended to be run via cron every minute. 
It gathers the last minute of cadvisor stats, rolls them up and then
uploads stats to the collector endpoint. 

For this script to run, cadvisor (https://github.com/google/cadvisor) must be running
along with the collector (see collector/collector.py).

Environment variable examples:

    # The URL for the cadvisor API
    CADVISOR_URL=http://192.168.222.5:8989/api/v1.2

    # The URL for the collector endpoint
    COLLECTOR_URL=http://192.168.222.5:8787/cadvisor/metrics

Running:

    python sender.py

"""
import time
import json
import requests
import uuid
import dateutil.parser
import os

# Determine the collector URL. The default collector.local address is used to make running via docker easier.
endpoint = os.getenv('COLLECTOR_URL', 'http://collector.local:8787/cadvisor/metrics/')

# Determine the cadvisor URL. The default cadvisor.local address is used to make running via docker easier.
# Note that port 8989 is being used below, which is not the standard port given in cadvisor's documentation examples.
cadvisor_base = os.getenv('CADVISOR_URL', 'http://cadvisor.local:8989/api/v1.2')

# The following functions are examples of different approaches for detemining which containers to report stats on

def match_all_but_cadvisor(name):
    """
    Match on anything that isn't cadvisor. 
    """
    if 'cadvisor' in value['aliases']:
        return False
    return True

def match_all(name):
    """
    Match on all containers, including cadvisor.
    """
    return True

def match_on_uuid(name):
    """
    Match on any container that has a UUID as its name.

    This is used internally at Catalyze, as all of our customer containers have a UUID as their container name.
    """

    if 'cadvisor' in value['aliases']:
        return False # Ignore cadvisor

    try:
        # Try to parse as a UUID and match if it works
        uuid.UUID(name, version=1)
        return True
    except ValueError:
        # Not a UUID so not a container to match on
        return False


# Set the function to use when determining which containers to report on
# Set the MATCH_TYPE environment variable to UUID or NO_CADVISOR to change the function to use
match_type_name = os.getenv('MATCH_TYPE', 'ALL')
match_container_name = match_all # Default
if match_type_name == 'UUID':
    match_container_name = match_on_uuid
elif match_type_name == 'NO_CADVISOR':
    match_container_name = match_all_but_cadvisor

def total_min_max(stat, s_total, s_min, s_max):
    """
    Given a value (stat), add it to the total and check if it is the new
    min value or max value compared to s_min and s_max.
    """
    result_min = s_min
    result_max = s_max
    s_total += stat
    if s_min == None or stat < s_min:
        result_min = stat
    if s_max == None or stat > s_max:
        result_max = stat
    return s_total, result_min, result_max

def process_diskio(diskio, field):
    """
    Sum up all the disk IO bytes for a given field (Sync, Async, Read, Write).

    Only considering io_service_bytes stats right now (io_serviced is ignored).
    """

    total = 0
    io_stats = diskio['io_service_bytes']
    for entry in io_stats:
        total += entry['stats'][field]

    return total
        

# Connect to cadvisor and get the last minute's worth of stats (should be 60 stats per container)
r = requests.get('%s/docker' % cadvisor_base)
entries = []
for key, value in r.json().items():

    # Determine if one of the aliases matches (is something we want to collect metrics for)
    container_name = None
    for name in value['aliases']:
        if match_container_name(name):
            container_name = name
            break

    # Skip this if the container didn't match
    if container_name == None:
        continue

    # Compute the timestamp, using the first second in this series
    ts = int(dateutil.parser.parse(value['stats'][0]['timestamp']).strftime('%s'))
    
    # Run through all the stat entries for this container
    stats = value['stats']
    stats_len = len(stats) # Should always be 60

    # Initialize min/max/total variables for network KB, packets, memory, cpu
    total_memory = 0
    min_memory = None
    max_memory = None
    total_load = 0
    min_load = None
    max_load = None
    total_tx_bytes = 0
    min_tx_bytes = None
    max_tx_bytes = None
    total_rx_bytes = 0
    min_rx_bytes = None
    max_rx_bytes = None
    total_tx_packets = 0
    min_tx_packets = None
    max_tx_packets = None
    total_rx_packets = 0
    min_rx_packets = None
    max_rx_packets = None

    for stat in stats:

        # Grab the memory usage stats
        memory = stat['memory']
        memory_kb = memory['usage']/1024
        total_memory, min_memory, max_memory = total_min_max(memory_kb, total_memory, min_memory, max_memory)
    
        # Get the CPU stats. The load value is always 0?
        cpu = stat['cpu']
        cpu_load = cpu['load_average']
        total_load, min_load, max_load = total_min_max(cpu_load, total_load, min_load, max_load)

        # Grab the network stats
        network = stat['network']
        total_tx_bytes, min_tx_bytes, max_tx_bytes = total_min_max(network['tx_bytes'], total_tx_bytes, min_tx_bytes, max_tx_bytes)
        total_rx_bytes, min_rx_bytes, max_rx_bytes = total_min_max(network['rx_bytes'], total_rx_bytes, min_rx_bytes, max_rx_bytes)
        total_tx_packets, min_tx_packets, max_tx_packets = total_min_max(network['tx_packets'], total_tx_packets, min_tx_packets, max_tx_packets)
        total_rx_packets, min_rx_packets, max_rx_packets = total_min_max(network['rx_packets'], total_rx_packets, min_rx_packets, max_rx_packets)
        # Not handling drops right now for simplicity

    # Initialize the entry for this container
    entry = {'name': container_name, 'ts' : ts}
    entry['cpu'] = {}
    entry['memory'] = {}
    entry['network'] = {}
    entry['diskio'] = {}

    # Compute first/last values of cumulative counters
    first = stats[0] # First item in this series
    last = stats[stats_len-1] # Last item in this series

    # Compute CPU usage delta
    start_cpu_usage = first['cpu']['usage']['total']
    end_cpu_usage = last['cpu']['usage']['total']

    # Compute Disk IO deltas
    start_async_bytes = process_diskio(first['diskio'], 'Async')
    end_async_bytes = process_diskio(last['diskio'], 'Async')
    start_sync_bytes = process_diskio(first['diskio'], 'Sync')
    end_sync_bytes = process_diskio(last['diskio'], 'Sync')
    start_read_bytes = process_diskio(first['diskio'], 'Read')
    end_read_bytes = process_diskio(last['diskio'], 'Read')
    start_write_bytes = process_diskio(first['diskio'], 'Write')
    end_write_bytes = process_diskio(last['diskio'], 'Write')

    # Add CPU stats
    entry['cpu']['usage'] = end_cpu_usage - start_cpu_usage
    entry['cpu']['load'] = {}
    entry['cpu']['load']['ave'] = total_load/stats_len
    entry['cpu']['load']['min'] = min_load
    entry['cpu']['load']['max'] = max_load

    # Add memory stats
    entry['memory']['ave'] = total_memory/stats_len
    entry['memory']['min'] = min_memory
    entry['memory']['max'] = max_memory

    # Add network stats
    entry['network']['tx_bytes'] = {}
    entry['network']['tx_bytes']['ave'] = total_tx_bytes/stats_len 
    entry['network']['tx_bytes']['min'] = min_tx_bytes
    entry['network']['tx_bytes']['max'] = max_tx_bytes
    entry['network']['rx_bytes'] = {}
    entry['network']['rx_bytes']['ave'] = total_rx_bytes/stats_len 
    entry['network']['rx_bytes']['min'] = min_rx_bytes
    entry['network']['rx_bytes']['max'] = max_rx_bytes
    entry['network']['tx_packets'] = {}
    entry['network']['tx_packets']['ave'] = total_tx_packets/stats_len 
    entry['network']['tx_packets']['min'] = min_tx_packets
    entry['network']['tx_packets']['max'] = max_tx_packets
    entry['network']['rx_packets'] = {}
    entry['network']['rx_packets']['ave'] = total_rx_packets/stats_len 
    entry['network']['rx_packets']['min'] = min_rx_packets
    entry['network']['rx_packets']['max'] = max_rx_packets
    # Note that errors are not being reported, easy enough to add

    # Add disk IO stats
    # These stats are currently aggregated across all volumes. May not be desirable.
    entry['diskio']['async'] = end_async_bytes - start_async_bytes
    entry['diskio']['sync'] = end_sync_bytes - start_sync_bytes
    entry['diskio']['read'] = end_read_bytes - start_read_bytes
    entry['diskio']['write'] = end_write_bytes - start_write_bytes
    # Note that io_serviced stats are not being included here. Easy to add if needed.

    entries.append(entry)

interval = 60 # Number of seconds of data that we are getting back from cadvisor per container

# Create the final result to send to the collector
stats_result = {}
stats_result['timestamp'] = int(time.time()) # Epoch time for when this entry was computed (in seconds)
stats_result['interval'] = interval # The duration of this stat entry in seconds
stats_result['stats'] = entries

# Sending along info about the host, as tracked by cadvisor
# This data can easily be extended to include other sources (/proc, etc)
r = requests.get('%s/machine' % cadvisor_base)
stats_result['machine'] = r.json()

print(json.dumps(stats_result))

# POST the result to the collector
headers = {'content-type': 'application/json'}
post_result = requests.post(endpoint, data=json.dumps(stats_result), headers=headers)
post_result.raise_for_status()
