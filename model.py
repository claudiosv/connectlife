from pydantic import BaseModel, Field


class HisenseACStatus(BaseModel):
    # --- Writable / Target States (t_) ---

    t_power: int = Field(..., description="Power state of the AC (0 = Off, 1 = On)")
    t_work_mode: int = Field(
        ...,
        description="Operating mode (e.g., 0 = Cool, 1 = Heat, 2 = Fan, 3 = Dry, 4 = Auto)",
    )
    t_temp: int = Field(..., description="Target temperature setpoint (e.g., 68)")
    t_temp_type: int = Field(
        ...,
        description="Temperature unit/scale (0 = Celsius, 1 = Fahrenheit). '1' aligns with the 61/68 values.",
    )
    t_fan_speed: int = Field(
        ...,
        description="Fan speed setting (e.g., 0 = Auto, 1 = Low, 2 = Medium, 3 = High)",
    )
    t_up_down: int = Field(
        ...,
        description="Vertical louver swing toggle for directing airflow up and down (0 = Off, 1 = On)",
    )
    t_super: int = Field(
        ...,
        description="Super/Turbo mode toggle for rapid cooling or heating (0 = Off, 1 = On)",
    )
    t_sleep: int = Field(
        ...,
        description="Sleep mode toggle, which gradually adjusts temperature for comfort (0 = Off, 1 = On)",
    )
    t_fan_mute: int = Field(
        ...,
        description="Quiet/Mute mode toggle to run the fan at the lowest possible noise level (0 = Off, 1 = On)",
    )

    # --- Read-Only / Feedback States (f_) ---

    f_temp_in: int = Field(
        ...,
        description="Current indoor ambient temperature reading from the AC's internal sensor (e.g., 61)",
    )

    # --- Error / Fault Flags (f_e_) ---

    f_e_upmachine: int = Field(
        ...,
        description="Error code/status for the 'up' machine, commonly referring to the indoor head unit (0 = Normal)",
    )
    f_e_dwmachine: int = Field(
        ...,
        description="Error code/status for the 'down' machine, commonly referring to the outdoor compressor unit (0 = Normal)",
    )
    f_e_intemp: int = Field(
        ...,
        description="Error flag for the indoor ambient temperature thermistor/sensor (0 = Normal)",
    )
    f_e_incoiltemp: int = Field(
        ...,
        description="Error flag for the indoor evaporator coil temperature sensor (0 = Normal)",
    )
    f_e_outcoiltemp: int = Field(
        ...,
        description="Error flag for the outdoor condenser coil temperature sensor (0 = Normal)",
    )
    f_e_waterfull: int = Field(
        ...,
        description="Water full error flag, typical for portable units or in dehumidify mode (0 = Normal)",
    )
    f_e_push: int = Field(
        ...,
        description="Error flag indicating a failure in pushing state updates or notifications to the cloud (0 = Normal)",
    )

    # --- Matter Protocol / Device Metadata ---

    f_matterOriginalVendorId: int = Field(
        ...,
        description="The original Vendor ID assigned to Hisense/ConnectLife by the CSA for the Matter smart home protocol",
    )
    f_matterOriginalProductId: int = Field(
        ...,
        description="The original Product ID for this specific AC model under the Matter protocol",
    )
    f_matterUniqueId: str = Field(
        ...,
        description="A unique hexadecimal identifier/MAC address for this specific device on the Matter network",
    )
    f_addmatterdevice: int = Field(
        ...,
        description="Flag indicating if the device has been provisioned/added as a Matter ecosystem device (1 = Yes)",
    )
