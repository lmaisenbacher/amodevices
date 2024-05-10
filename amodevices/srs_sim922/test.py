from amodevices import srsSim922
import logging
import time

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

device = {
    "Device": "Cavity Temperature Monitor",
    "Model": "SRS SIM922",
    "Address": "COM5",
    "SerialConnectionParams":
        {
            "baudrate": 9600,
            "bytesize": 8,
            "stopbits": 1,
            "parity": "N"
        },
    "Timeout": 1,
    "ParallelReadout": True,
    "tags": {},
    "measurement": "cavitytemperature",
        "Channels":
        {
            "TempSensor1":
            {
                "Type": "Temperature",
                "field-key": "temperature",
                "tags":
                {
                    "sensor": "Sensor1",
                    "SRSSIM922ChannelName": "1",
                    "unit": "K"
                }
            },
            "TempSensor2":
            {
                "Type": "Temperature",
                "field-key": "temperature",
                "tags":
                {
                    "sensor": "Sensor2",
                    "SRSSIM922ChannelName": "2",
                    "unit": "K"
                }
            },
            "TempSensor3":
            {
                "Type": "Temperature",
                "field-key": "temperature",
                "tags":
                {
                    "sensor": "Sensor3",
                    "SRSSIM922ChannelName": "3",
                    "unit": "K"
                }
            },
            "TempSensor4":
            {
                "Type": "Temperature",
                "field-key": "temperature",
                "tags":
                {
                    "sensor": "Sensor4",
                    "SRSSIM922ChannelName": "4",
                    "unit": "K"
                }
            }
            }
}

logger.info('creating device instance')

device_instance = srsSim922(device)

logger.info('Performing 10 readings seperated by 1 second each...')
for i in range(10):
    readings = device_instance.get_values()
    logger.info(f'Reading {i}:')
    for chan_id, value in readings.items():
        logger.info(f'channel id: {chan_id}   Measured value: {value}')
    time.sleep(1)
logger.info("Completed 10 Successful Readings.")
