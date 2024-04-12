# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES.
# SPDX-FileCopyrightText: All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# %%
"""
Running Ensemble Inference
==========================

Simple ensemble inference workflow.

This example will demonstrate how to run a simple inference workflow to generate a
ensemble forecast using one of the built in models of Earth-2 Inference
Studio.

In this example you will learn:

- How to instantiate a built in prognostic model
- Creating a data source and IO object
- Select a perturbation method
- Running a simple built in workflow
- Post-processing results
"""

# %%
# Creating a Simple Ensemble Workflow
# -----------------------------------
#
# To start lets begin with creating a simple ensemble workflow to use. We encourage
# users to explore and experiment with their own custom workflows that borrow ideas from
# built in workflows inside :py:obj:`earth2studio.run` or the examples.
#
# Creating our own generalizable ensemble workflow is easy when we rely on the component
# interfaces defined in Earth2Studio (use dependency injection). Here we create a run
# method that accepts the following:
#
# - time: Input list of datetimes / strings to run inference for
# - nsteps: Number of forecast steps to predict
# - nensemble: Number of ensembles to run for
# - prognostic: Our initialized prognostic model
# - perturbation_method: Our initialized pertubation method
# - data: Initialized data source to fetch initial conditions from
# - io: IOBackend

# %%
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv

load_dotenv()  # TODO: make common example prep function

import numpy as np
import torch
from loguru import logger
from tqdm import tqdm

from earth2studio.data import DataSource, fetch_data
from earth2studio.io import IOBackend
from earth2studio.models.px import PrognosticModel
from earth2studio.perturbation import PerturbationMethod
from earth2studio.utils.coords import map_coords, extract_coords
from earth2studio.utils.time import to_time_array

logger.remove()
logger.add(lambda msg: tqdm.write(msg, end=""), colorize=True)


def run_ensemble(
    time: list[str] | list[datetime] | list[np.datetime64],
    nsteps: int,
    nensemble: int,
    prognostic: PrognosticModel,
    perturbation_method: PerturbationMethod,
    data: DataSource,
    io: IOBackend,
) -> IOBackend:
    """Simple ensemble workflow

    Parameters
    ----------
    time : list[str] | list[datetime] | list[np.datetime64]
        List of string, datetimes or np.datetime64
    nsteps : int
        Number of forecast steps
    nensemble : int
        Number of ensemble members to run inference for.
    prognostic : PrognosticModel
        Prognostic models
    perturbation_method : PerturbationMethod
        Method of perturbing the initial condition to form an ensemble.
    data : DataSource
        Data source
    io : IOBackend
        IO object

    Returns
    -------
    IOBackend
        Output IO object
    """
    logger.info("Running simple workflow!")
    # Load model onto the device
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info(f"Inference device: {device}")
    prognostic = prognostic.to(device)
    # Fetch data from data source and load onto device
    time = to_time_array(time)
    x, coords = fetch_data(
        source=data,
        time=time,
        lead_time=prognostic.input_coords["lead_time"],
        variable=prognostic.input_coords["variable"],
        device=device,
    )
    logger.success(f"Fetched data from {data.__class__.__name__}")

    # Expand x, coords for ensemble
    x = x.unsqueeze(0).repeat(nensemble, *([1] * x.ndim))
    coords = {"ensemble": np.arange(nensemble)} | coords

    # Set up IO backend
    total_coords = coords.copy()
    total_coords["lead_time"] = np.asarray(
        [prognostic.output_coords["lead_time"] * i for i in range(nsteps + 1)]
    ).flatten()

    var_names = total_coords.pop("variable")
    io.add_array(total_coords, var_names)

    # Map lat and lon if needed
    x, coords = map_coords(x, coords, prognostic.input_coords)

    # Perturb ensemble
    x += perturbation_method(x, coords)

    # Create prognostic iterator
    model = prognostic.create_iterator(x, coords)

    logger.info("Inference starting!")
    with tqdm(total=nsteps + 1, desc="Running inference") as pbar:
        for step, (x, coords) in enumerate(model):
            io.write(*extract_coords(x, coords))
            pbar.update(1)
            if step == nsteps:
                break

    logger.success("Inference complete")
    return io


# %%
# Set Up
# ------
# With the ensemble workflow defined, we now need to create the indivdual components.
#
# We need the following:
#
# - Prognostic Model: Use the built in FourCastNet model :py:class:`earth2studio.models.px.FCN`.
# - perturbation_method: Use the Spherical Gaussian Method :py:class:`earth2studio.perturbation.SphericalGaussian`.
# - Datasource: Pull data from the GFS data api :py:class:`earth2studio.data.GFS`.
# - IO Backend: Lets save the outputs into a Zarr store :py:class:`earth2studio.io.ZarrBackend`.
#
# %%
import numpy as np
import torch
from collections import OrderedDict
from typing import Union, List

from earth2studio.models.px import FCN
from earth2studio.perturbation import PerturbationMethod, SphericalGaussian
from earth2studio.data import GFS
from earth2studio.io import ZarrBackend
from earth2studio.utils.type import CoordSystem

# Load the default model package which downloads the check point from NGC
package = FCN.load_default_package()
model = FCN.load_model(package)

# Instantiate the pertubation method
sg = SphericalGaussian(noise_amplitude=0.05)

# Create the data source
data = GFS()

# Create the IO handler, store in memory
chunks = {"ensemble": 1, "time": 1}
io = ZarrBackend(file_name="outputs/ensemble_sg.zarr", chunks=chunks)

# %%
# Execute the Workflow
# --------------------
# With all componments intialized, running the workflow is a single line of Python code.
# Workflow will return the provided IO object back to the user, which can be used to
# then post process. Some have additional APIs that can be handy for post-processing or
# saving to file. Check the API docs for more information.
#
# For the forecast we will predict for two days (these will get executed as a batch) for
# 20 forecast steps which is 5 days.
# %%

nsteps = 10
nensemble = 8
io = run_ensemble(["2024-01-01"], nsteps, nensemble, model, sg, data, io)

# %%
# Post Processing
# ---------------
# The last step is to post process our results. Cartopy is a greate library for plotting
# fields on projects of a sphere.
#
# Notice that the Zarr IO function has additional APIs to interact with the stored data.

# %%
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import cartopy.feature as cfeature
from matplotlib.colors import TwoSlopeNorm

forecast = "2024-01-01"
variable = "t2m"
step = 0  # lead time = 24 hrs

plt.close("all")
# Create a Robinson projection
projection = ccrs.Robinson()

# Create a figure and axes with the specified projection
fig, (ax1, ax2, ax3) = plt.subplots(
    nrows=1, ncols=3, subplot_kw={"projection": projection}, figsize=(12, 5)
)


def plot_(axi, data, title):
    """Convenience function for plotting pcolormesh."""
    # Plot the field using pcolormesh
    im = axi.pcolormesh(
        io["lon"][:],
        io["lat"][:],
        data,
        transform=ccrs.PlateCarree(),
        cmap="coolwarm",
    )
    plt.colorbar(im, ax=axi)
    # Set title
    axi.set_title(title)
    # Add coastlines and gridlines
    axi.coastlines()
    axi.gridlines()


plot_(
    ax1,
    io[variable][0, 0, step],
    f"{forecast} - Lead time: {6*step}hrs - Member: {0}",
)
plot_(
    ax2,
    io[variable][1, 0, step],
    f"{forecast} - Lead time: {6*step}hrs - Member: {1}",
)
plot_(
    ax3,
    np.std(io[variable][:, 0, step], axis=0),
    f"{forecast} - Lead time: {6*step}hrs - Std",
)

plt.savefig(f"outputs/02_{forecast}_{variable}_{step}_ensemble.jpg")