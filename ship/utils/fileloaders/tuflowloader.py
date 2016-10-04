"""

 Summary:
    Contains the TuflowLoader class.
    This is used to read in the files that make up a Tuflow model. i.e. the
    tcf, tgc, etc files that the model requires to be run.
    Returns a Tcf object that contains references to the other model file
    types and all of the files identified within those models

 Author:  
     Duncan Runnacles

  Created:  
     01 Apr 2016

 Copyright:  
     Duncan Runnacles 2016

 TODO:
    Given file must be a .tcf at the  moment. This should be changed so that an
    .ecf can be provided as well.

 Updates:

"""


import os
import hashlib
import re
import random

from ship.utils import filetools
from ship.utils.fileloaders.loader import ALoader
from ship.utils import utilfunctions as uf
from ship.utils.atool import ATool
from ship.tuflow.tuflowmodel import TuflowModel, TuflowTypes, ModelRef, ModelOrder
from ship.tuflow.tuflowmodelfile import TuflowModelFile, TuflowScenario, TuflowEvent
from ship.tuflow import FILEPART_TYPES as ft
from ship.tuflow import tuflowfilepart as tfp

import logging
logger = logging.getLogger(__name__)
"""logging references with a __name__ set to this module."""


class TuflowLoader(ATool, ALoader):
    """Loads all the data referenced by a .tcf file.
    
    Calling the :method:'load' function of this with a path to a tcf file
    will read in all of content of all the files it references.
    
    The load function will return an instance of :class:'TuflowModel'.
    
    See Also:
        TuflowModel
        ModelOrder
        TuflowTypes
        ModelRef
    """
    
    def __init__(self):
        """Initialise defaults"""

        ATool.__init__(self)
        ALoader.__init__(self)
        logger.debug('Initialising TuflowLoader')
        
        self.file_types = {'mif': tfp.GisFile, 'mid': tfp.GisFile, 
                           'shp': tfp.GisFile, 'tmf': tfp.DataFile,
                           'csv': tfp.DataFile}
        """Associates a range of different known file extensions with the
        Class types that deal with them.
        """

        self.types = TuflowTypes()
        """A lookup table containing the known keywords used as instructions
        in Tuflow files and the categories that they are put under.
        """
        

    def loadFile(self, tcf_path, arg_dict={}):
        """Loads all content referenced by the given path.
        
        The arg_dict dictionary is used to pass in any values needed at runtime
        for resolving scenario and event logic in the control files. These are
        the ~s~ and ~e~ values that are specified either in the tcf file or
        on the command line/batch file. They are submitted as two separate
        sub-dicts that contain the values for this run. E.g:
        ::
            > tuflow.exe -s1 scen1 -e1 100yr -e2 4h myfile_~s1~_~e1~_~e2~_place.tcf  
            
            To provide these variables for use by the tuflow loader, so it would
            know how to load any .tcf file with those tilde values in, you could
            do:  
            
            tcfpath = 'C:\path\to\tcf\myfile_~s1~_~e1~_~e2~_place.tcf'
            args_dict = {'scenario': {'s1': 'scen1'}, 'event': {'e1': '100yr', 'e2': '4h'}}
            loader = FileLoader()
            tuflow_model = loader.loadFile(tcfpath, args_dict)
            
            Events and scenarios can be provided that do not have placeholders
            in the filename. When this is the case tuflow will add them to the
            end of the filename. For example:
            
            tcfpath = 'C:\path\to\tcf\myfile_~s1~_~e1~_place.tcf'
            args_dict = {'scenario': {'s1': 'scen1'}, 'event': {'e1': '100yr', 'e2': '4h'}}
            
            Would give a file name of 'C:\path\to\tcf\myfile_scen1_100yr_place4h.tcf'
        
        Args:
            tcf_path(str): path to a .tcf file.
            arg_dict={}(Dict): contains scenario variables for the tuflow model
                being loaded. E.g. tuflow files can be referenced using 
                charactars within tildes (~s~ or ~e~) to indicate placeholders.
                The scenario and event values are passed in as two separate 
                dicts under the main dict keys 'scenario' and 'event'.
        
        Raises:
            ValueError: if tcf_path is not a .tcf file.
            IOError: if tcf_path doesn't exist.
        """
        logger.info('Loading Tuflow model')
        if not uf.checkFileType(tcf_path, ext=['.tcf', '.TCF']):
            logger.error('File: ' + tcf_path + '\nDoes not match tcf extension (*.tcf or *.TCF)')
            logger.error('Illegal File Error: %s in not of type tcf' % (tcf_path))
            raise AttributeError ('Illegal File Error: %s in not of type tcf' % (tcf_path))
        
        # If the file doesn't exist raise an exception
        if not os.path.exists(tcf_path) or not os.path.isabs(tcf_path):
            logger.error('.tfc path must exist and MUST be absolute')
            raise IOError ('Tcf file does not exist')
        
        self.scenario_vals = {}
        """Any scenario values that are passed through."""
        
        if 'scenario' in arg_dict.keys():
            self._has_scenario = True 
            self.scenario_vals = arg_dict['scenario']
        
        self.event_vals = {}
        """Any event values that are passed through."""
        
        if 'event' in arg_dict.keys(): 
            self._has_event = True 
            self.event_vals = arg_dict['event']

        self.tuflow_model = TuflowModel()
        """TuflowModel class instance"""
        
        self._file_queue = uf.FileQueue()
        """FileQueue instance. Used to store all TuflowModelFile references
        found during loading so that they can be loaded in order.
        """
        
        self._model_order = ModelOrder()
        """Keeps track of the order of the files loaded in. Is given to the
        TuflowModel object so that it knows which files reference which 
        other files
        """
        
        self._global_order = 0
        """Tracks the number of file parts."""
        
#         self._added_control_files = []
        """Tracks loaded control files to avoid loading the same one twice."""

        self._in_output_define = False
        """Very hacky way of dealing with the fact the matching End Define's.
        
        Both the Event logic and the Define Output Zones commands use End Define
        to close the switch. This is used in very simplistic terms to stop the
        loader from trying to close a non-existent event if statement when it
        finds an End Define from an output zone.
        """
        
        self._if_type = None
        """Also hacky notes whether in a scenario or an event.
        
        Currently only ever set to None, 'EVT' or 'SCEN'
        """
        
        self._cur_event_text = None
        self._cur_event_name = None
        self._cur_event_source = {}

        self._fetchTuflowModel(tcf_path)
        self.tuflow_model.model_order = self._model_order
        self.tuflow_model.scenario_vals = self.scenario_vals
        self.tuflow_model.event_vals = self.event_vals
        
        if not self.tuflow_model.missing_model_files:
            self.buildSourceData()
        
        return self.tuflow_model

    
    def buildSourceData(self):
        """Creates a dict from loaded TuflowVariables.
        
        Creates two dict's to hold event source data by searching through all 
        of the ModelVariables loaded. It creates two sets:  
            - Those that fall within the current scenario/event vals only.
            - All that are noted in the file.
        
        If no event source references can be found it falls back on using the
        last read BC Event Text and BC Event Name for the cur_event_source and
        all other references as well for all_event_source. This is because there
        can be multiple calls to both (although doing this with text should be
        avoided). When passing the values to the DATA type files the currently
        set values are given, which will definitely correct. It is not possible 
        to have both BC Event Source and BC Event Text/BC Event Name in the 
        same model.
            
        This method then adds a copy of the new dict's to the loaded TuflowModel.
        """
        variables = self.tuflow_model.getVariables(se_only=True, no_duplicates=True)
        cur_source = {}
        all_source = {}
        found_source = False
        for v in variables:
            if v.command.upper() == 'BC EVENT SOURCE':
                found_source = True
                if not v.key_var in cur_source.keys():
                    cur_source[v.key_var] = v.value_var
                
        variables = self.tuflow_model.getVariables(se_only=False)
        for v in variables:
            if v.command.upper() == 'BC EVENT SOURCE':
                if not v.key_var in all_source.keys():
                    all_source[v.key_var] = []
                all_source[v.key_var].append(v.value_var)

        if not found_source:
            if not self._cur_event_text is None and not self._cur_event_name is None:
                cur_source[self._cur_event_text] = self._cur_event_name
                all_source = self._cur_event_source
        self.tuflow_model.cur_event_source = cur_source
        self.tuflow_model.all_event_source = all_source
        
        # Set the event source data for the data type files
#         data = self.tuflow_model.getFiles(ft.DATA)
#         for d in data:
#             d.addEventSourceData(cur_source)
        parts = self.tuflow_model.getTuflowFileParts()
        for p in parts:
            p.addEventSourceData(cur_source)

    
    def _fetchTuflowModel(self, tcf_path):
        """Does the actual loading"""
        
        # Load the file load details into the class
        model_details = [tcf_path, None, None, ''] 
        file_d = self._FileDetails(model_details, True)
        if not file_d.getFile(): raise IOError
        model = file_d.getModel()
        
        # Add reference to the model route to the tuflow_model
        self.tuflow_model.root = file_d.root

        # Add file reference to the appropriate structures
        # Handed true because it is the root node.
        self._model_order.addRef(ModelRef(file_d.head_hash, file_d.extension), True)
        
        # Need to create one here becuase it's the first
        tmf_hash = file_d.generateModelfileHash(tcf_path)
        line_val = file_d.generateModelTuflowFile(ft.MODEL, self._global_order, tmf_hash)
        line_val.tmf_hash = tmf_hash
        self._global_order += 1
        self.tuflow_model.mainfile = line_val
        self.tuflow_model.files[file_d.extension][file_d.head_hash] = model

        # Parse the contents into object form
        self._parseFileContents(file_d, model)
        
        # Do the same thing again for all the other files found
        while not self._file_queue.isEmpty():
            
            model_details = self._file_queue.dequeue()
            file_d = self._FileDetails(model_details, False)
            
            # Check to see if this control file has already been loaded. If it
            # has then don't load it again
            if file_d.head_hash in self.tuflow_model.files[file_d.extension]:
                continue
            
            success = file_d.getFile()
            if success == False:
                self.tuflow_model.missing_model_files.append(file_d.filename)
                self.addWarning('Missing File', 'File could not be loaded at: ' + file_d.filename)
                continue
            
            else:
                model = file_d.getModel()
                self._model_order.addRef(ModelRef(file_d.head_hash, file_d.extension, 
                                                            file_d.parent_hash))
                self.tuflow_model.files[file_d.extension][file_d.head_hash] = model
                self._parseFileContents(file_d, model)


    def _parseFileContents(self, file_d, model):
        """Parse the current file contents and build all the objects.
        
        Creates an object for (almost*) each line in the file and adds it to
        the object and reference lists. 
        
        There are a few places that keep refs to these objects 
        (the TuflowModelFile, TuflowModel) for various reasons. Only 
        TuflowModel.file_parts actually stores the object though.
        
        Args:
            file_d(_FileDetails): stores info about the current file.
            model(TuflowModel): stores references to the loaded objects.
        
        * If there are multiple comments or unknown lines in a row they will
          be lumped together.
        """
        def _clearUnknownContents(file_d, line, model, unknown_contents):
            """Stash and clear any unkown stuff."""
            
            unknown_contents[-1] = unknown_contents[-1].rstrip()
            model.addContent(ft.COMMENT, unknown_contents)
            unknown_contents = []
            return unknown_contents

        
        scenario_stack = uf.LoadStack()
        scenario_order = 0
        event_stack = uf.LoadStack()
        event_order = 0
        unknown_contents = []
        for line in file_d.raw_contents:
            line_contents = self._getLineDetails(line, file_d)
            line_type = line_contents[0][2]

            if line_type == ft.COMMENT or line_type == ft.UNKNOWN:
                unknown_contents.append(line_contents[0][0])
            
            # Add or remove an if-else scenario object from the stack. When they
            # are removed they are added to the model. 
            elif line_type == ft.SCENARIO:
                # Stash and clear any unknown stuff first if it's there.
                if unknown_contents:
                    unknown_contents = _clearUnknownContents(file_d, line, model, 
                                                             unknown_contents)
                model, scenario_stack, scenario_order = self.buildScenario(
                                                           line_type,
                                                           line_contents[0][0], 
                                                           line_contents[0][1], 
                                                           scenario_order, 
                                                           scenario_stack,
                                                           model)
            
            elif line_type == ft.EVENT:
                # Stash and clear any unknown stuff first if it's there.
                if unknown_contents:
                    unknown_contents = _clearUnknownContents(file_d, line, model, 
                                                             unknown_contents)
                model, event_stack, event_order = self.buildEvent(
                                                           line_type,
                                                           line_contents[0][0], 
                                                           line_contents[0][1], 
                                                           event_order, 
                                                           event_stack,
                                                           model)
            
            else:
                # Stash and clear any unknown stuff first if it's there.
                if unknown_contents:
                    unknown_contents = _clearUnknownContents(file_d, line, model, 
                                                             unknown_contents)
                
                # Add the new object to the TuflowModelFile
                for l in line_contents:
                    
                    line_val, hex_hash, line_type, ext = l

                    # DEBUG - probably move all MODEL specific logic here, but
                    #         check doesn't need to be further down first
                    if line_type == ft.MODEL:
                        rel_root = ''
                        if not line_val.relative_root is None:
                            rel_root = line_val.relative_root
                        
                        self._file_queue.enqueue([line_val.getAbsolutePath(), 
                                                  line_val.tmf_hash, 
                                                  file_d.head_hash, rel_root])
                    model.addContent(line_type, line_val)
                    
                    # Add the hash refs to the scenario and event objects if
                    # we have any currently active
                    if not scenario_stack.isEmpty():
                        if line_type == ft.EVENT or line_type == ft.EVENT_END:
                            scenario_stack.peek().addPartRef(TuflowScenario.EVENT_PART, 
                                                              hex_hash)
                        else:
                            scenario_stack.peek().addPartRef(TuflowScenario.TUFLOW_PART, 
                                                              hex_hash)
                    if not event_stack.isEmpty():
                        event_stack.peek().addPartRef(hex_hash)
                    
        # Make sure we clear up any leftovers
        if unknown_contents:
            unknown_contents = _clearUnknownContents(file_d, line, model, 
                                                     unknown_contents)
            
    
    def _getLineDetails(self, line, file_d):
        """Constructs the appropriate object from the file line.
        
        Takes a line from the file and decides what to do with it...and does
        it. This includes:
        # Checking if it's a comment or has any contents.
        # seperating the components if it's an instruction.
        # Deciding on what type of object to create (if any)
        # Creating the appropriate object and returning it. 
        
        This function is a bit messy, but it's hard to break up. It's 
        tempting to add a lot of it to :class:'TuflowFilePart' and let it
        deal with it in a nice polymorphic manner.
        
        Args:
            line(str): the file line to be dealt with.
            file_d(_FileDetails): object containing the current file data.
        
        Returns:
            Tuple - (line_val, hex_hash, command_type, ext):
                # the object created from the line.
                # the hexidecimal hash value created for the object.
                # the category that the object has been put in.
                # the extension of the file or '' if none found.
        """
        hex_hash = file_d.generateHash(file_d.filename + line + str(
                                                        file_d.parent_hash))
        line = line.lstrip()
        upline = line.upper()
        line_val = '\n'
        ext = ''
        
        # It's a comment or blank line
        if line.startswith('#') or line.startswith('!'):
            line_val = line
            command_type = ft.COMMENT 
            
        elif line.strip() == '':
            command_type = ft.COMMENT
        
        # If scenario statement
        elif self._if_type != 'EVT' and (upline.startswith('IF SCENARIO') or upline.startswith('ELSE IF') \
                    or upline.startswith('END IF') or upline.startswith('ELSE')):
            self._if_type = 'SCEN'
            command_type = ft.SCENARIO
            line_val = line
        
        # Define event statement
        elif (self._if_type != 'SCEN' and upline.startswith('IF EVENT') or upline.startswith('END IF') or \
                    upline.startswith('IF ELSE') or upline.startswith('ELSE')) or \
                    upline.startswith('DEFINE EVENT') or (upline.startswith('END DEFINE') \
                    and not self._in_output_define):
            self._if_type = 'EVT'
            command_type = ft.EVENT
            line_val = line
        
        # TODO - Hacky fix for matching End Defines with Event logic.
        #        Output zones use same closure which causing false catches.
        elif line.strip().upper().startswith('END DEFINE') and self._in_output_define:
            line_val = line
            command_type = ft.COMMENT
            self._in_output_define = False
        elif line.strip().upper().startswith('DEFINE OUTPUT ZONE'):
            line_val = line
            command_type = ft.COMMENT
            self._in_output_define = True
        
        elif '==' in line or 'AUTO' in line.upper():

            # Estry AUTO 
            if 'AUTO' in line.upper():
                line = self._breakAuto(line, file_d.filename)
                self.tuflow_model.has_estry_auto = True

            command, instruction = self._breakLine(line)
            found, command_type = self.types.find(command.upper())
            
            # TODO: Not sure about this at the moment. I think these should be
            #       going into UNKNOWN FILE, if a file, so we can still update 
            #       them when needed. Even if we don't know what they do?
            if not found:
                command_type = ft.UNKNOWN 
                line_val = line
            else:
                if command_type == ft.VARIABLE: 
                    
                    # If there's no scenario values handed in use the file
                    # specified ones
                    if 'MODEL SCENARIOS' in command.upper():
                        self.addScenarioVars(instruction)
                        
                    if command.strip().upper() == 'BC EVENT SOURCE':
                        line_val = tfp.ModelVariableKeyVal(self._global_order, 
                                                          instruction, hex_hash, 
                                                          command_type, command,
                                                          file_d.extension)
                    else:
                        line_val = tfp.ModelVariables(self._global_order, 
                                                      instruction, hex_hash, 
                                                      command_type, command,
                                                      file_d.extension)
                        if command.strip().upper() == 'BC EVENT TEXT':
                            self._cur_event_text = line_val.raw_var
                        elif command.strip().upper() == 'BC EVENT NAME':
                            self._cur_event_name = line_val.raw_var
                    self._global_order += 1

                else:
                    # Do a check for MI Projection and SHP projection. These can
                    # be either a WKT str or a file.
                    isfile = True
                    if command.upper() == 'MI PROJECTION' or command.upper() == 'SHP PROJECTION':
                        isfile = self._checkProjectionIsFile(instruction)

                    if not isfile:
                        line_val = line
                        command_type = ft.COMMENT 
                    else:
                        ext = self.extractExtension(instruction)
                        if (ext is None or ext == '') and not command_type == ft.RESULT: 
                            parent_path = os.path.join(file_d.root, file_d.relative_root)
                            instruction, ext = self._findFileType(parent_path, instruction)
                        f_type = self.file_types.get(ext)
                        
                        # It's a model file or a results file
                        # TODO: Not safe yet...needs more work.
                        if f_type == None:

                            line_val = tfp.ModelFile(self._global_order, instruction, 
                                                    hex_hash, command_type, 
                                                    command, file_d.extension, 
                                                    file_d.root, 
                                                    file_d.relative_root, ext)
                            line_val.tmf_hash = file_d.generateModelfileHash(line_val.getAbsolutePath())
                            self._global_order += 1

                            if command_type is ft.RESULT: 
                                line_val = self._resolveResult(line_val)
                                line_val.category = None
                        
                        # It's one of the files in self.types
                        else:
                            piped_files, pipe_hashes = self.checkForPipes(instruction,
                                                                        file_d, hex_hash)
                            multi_lines = []
                            for i, p in enumerate(piped_files):
                                hex_hash = pipe_hashes[i] 
                                
                                # If there's a piped file command we need to register
                                # associated files with each other
                                hex_hash = pipe_hashes[i]
                                child_hash = None
                                parent_hash = None
                                if len(piped_files) > i+1:
                                    child_hash = pipe_hashes[i+1]
                                if i > 0:
                                    parent_hash = pipe_hashes[i-1]
                                    
                                line_val = self.file_types[ext](self._global_order, 
                                                                p, hex_hash, 
                                                                command_type, command, 
                                                                file_d.extension,
                                                                file_d.root, 
                                                                file_d.relative_root,
                                                                parent_hash=parent_hash,
                                                                child_hash=child_hash)
                                if command_type == ft.DATA:
                                    line_val.evt_src_data = {self._cur_event_text: self._cur_event_name}
                                    self._cur_event_source[self._cur_event_text] = self._cur_event_name

                                multi_lines.append([line_val,
                                                   hex_hash,
                                                   command_type,
                                                   ext]) 
                                
                            # TODO: currently the same _global_order for all the
                            #       files in a piped command. Should they be 
                            #       different?
                            self._global_order += 1
                            return multi_lines

        else:
            command_type = ft.UNKNOWN 
            line_val = line
        
        # Needs to be return in a list because of the multi_lines setup above.
        return [[line_val, hex_hash, command_type, ext]]
    
    
    def _findFileType(self, parent_path, instruction):
        """
        """
        found, split, comchar = self.separateComment(instruction)
        
        ret_comment = ''
        if found:
            ret_comment = ' ' + comchar + split[1]
        
        f = split[0].strip()
        if os.path.exists(os.path.join(parent_path, f + '.mif')):
            return f + '.mif' + ret_comment, 'mif'
        elif os.path.exists(os.path.join(parent_path, f + '.mid')):
            return f + '.mid' + ret_comment, 'mid'
        elif os.path.exists(os.path.join(parent_path, f + '.shp')):
            return f + '.shp' + ret_comment, 'shp'
        else:
            return instruction, ''
        
    
    def buildScenario(self, line_type, line, hex_hash, scenario_order, 
                      scenario_stack, model):
        """
        """
        rettype, scenario = self.breakScenario(line, hex_hash, scenario_order)

        if rettype == 'IF':
            # If it's an embedded if we need to give a reference to the
            # parent TuflowScenario as well
            if not scenario_stack.isEmpty():
                scenario_stack.peek().addPartRef(TuflowScenario.SCENARIO_PART,
                                                  hex_hash)
            scenario_stack.add(scenario)
            scenario_order +=1
            model.addContent(line_type, hex_hash)

        elif rettype == 'ELSE':
            model.addContent(ft.SCENARIO_END, '')
            s = scenario_stack.pop()
            model.addScenario(s)
            scenario_order +=1
            if not scenario_stack.isEmpty():
                scenario_stack.peek().addPartRef(TuflowScenario.SCENARIO_PART,
                                                  hex_hash)
            scenario_stack.add(scenario)
            model.addContent(line_type, hex_hash)
        else:
            s = scenario_stack.pop()
            s.has_endif = True
            model.addContent(ft.SCENARIO_END, '')
            model.addScenario(s)
        
        return model, scenario_stack, scenario_order
        
    
    def breakScenario(self, instruction, hex_hash, order):
        """Breaks a scenario IF statement down into parts.
        
        Args:
            instruction(str): the if statement to break up.
            hex_hash(str): the hash code for this line.
            order(int): the order this scenario appears in the file.
            
        Return:
            tuple(str, TuflowScenario or None):  
        """
        found, split, comment_char = self.separateComment(instruction)
        comment = ''
        if found: comment = split[1].strip()
        instruction = split[0].strip()
        
        scenario = None
        upinstruction = instruction.upper()
        
        if 'ELSE' in upinstruction and not 'IF' in upinstruction:
            scen_vals = ['ELSE']
            return_type = 'ELSE'
            scenario = TuflowScenario(TuflowScenario.ELSE, scen_vals, hex_hash,
                                       order, comment, comment_char)
        elif 'ELSE IF SCENARIO' in upinstruction:
            scen_vals = instruction.split('==')[1].strip()
            scen_vals = scen_vals.split('|')
            scen_vals = [val.strip() for val in scen_vals]
            return_type = 'ELSE'
            scenario = TuflowScenario(TuflowScenario.ELSE, scen_vals, hex_hash,
                                       order, comment, comment_char)
        elif 'IF SCENARIO' in upinstruction:
            scen_vals = instruction.split('==')[1].strip()
            scen_vals = scen_vals.split('|')
            scen_vals = [val.strip() for val in scen_vals]
            return_type = 'IF'
            scenario = TuflowScenario(TuflowScenario.IF, scen_vals, hex_hash,
                                       order, comment, comment_char)       
        else:
            return_type = 'END'
        
        return return_type, scenario

    
    def buildEvent(self, line_type, line, hex_hash, event_order, 
                   event_stack, model):
        """
        """
        rettype, event = self.breakEvent(line, hex_hash, event_order)

        if rettype == 'DEFINE' or rettype == 'IF':
            event_stack.add(event)
            event_order +=1
            model.addContent(line_type, hex_hash)

        elif rettype == 'ELSE':
            model.addContent(ft.EVENT_END, '')
            e = event_stack.pop()
            model.addEvent(e)
            event_order += 1
            if not event_stack.isEmpty():
                event_stack.peek().addPartRef(hex_hash)
            event_stack.add(event)
            model.addContent(line_type, hex_hash)
        else:
            e = event_stack.pop()
            if rettype == 'END IF':
                e.has_endif = True
            else:
                e.has_enddefine = True
            model.addContent(ft.EVENT_END, '')
            model.addEvent(e)
        
        return model, event_stack, event_order
    
    
    def breakEvent(self, instruction, hex_hash, order):
        """Breaks a scenario IF statement down into parts.
        
        Args:
            instruction(str): the if statement to break up.
            hex_hash(str): the hash code for this line.
            order(int): the order this scenario appears in the file.
            
        Return:
            tuple(str, TuflowScenario or None):  
        """
        found, split, comment_char = self.separateComment(instruction)
        comment = ''
        if found: comment = split[1].strip()
        instruction = split[0].strip()
        upinstruction = instruction.upper()
        event = None
        
        if 'DEFINE EVENT' in upinstruction:
            evt_vals = instruction.split('==')[1].strip()
            evt_vals = evt_vals.strip()
            return_type = 'DEFINE'
            event = TuflowEvent(TuflowEvent.DEFINE, evt_vals, hex_hash,
                                       order, comment, comment_char)       
        elif upinstruction.startswith('IF EVENT'):
            evt_vals = instruction.split('==')[1].strip()
            evt_vals = evt_vals.strip()
            return_type = 'IF'
            event = TuflowEvent(TuflowEvent.IF, evt_vals, hex_hash,
                                       order, comment, comment_char)    
        
        elif 'ELSE IF EVENT' in upinstruction:
            evt_vals = instruction.split('==')[1].strip()
            evt_vals = evt_vals.strip()
            return_type = 'ELSE'
            event = TuflowEvent(TuflowEvent.ELSE, evt_vals, hex_hash,
                                       order, comment, comment_char)
        
        elif 'ELSE' in upinstruction:
            evt_vals = 'ELSE'
            return_type = 'ELSE'
            event = TuflowEvent(TuflowEvent.ELSE, evt_vals, hex_hash,
                                       order, comment, comment_char)
        
        elif 'END IF' in upinstruction:
            return_type = 'END IF'
        else:
            return_type = 'END DEFINE'
        
        return return_type, event
        
    
    def checkForTildes(self, instruction):
        """Check the file for tildes so we can try and replace them.
        
        Some filenames can caontain placeholders in the for ~something~. These
        correspond to scenario or event variables. This method checks the given
        file path to see if it has any of these and replaces them if it can.
        
        Args:
            instruction(str): the filename as read from file.
        
        Return:
            str - the filename with the tildes replaced, or False if none were found.
        """
        
        if '!' in instruction:
            comment_char = '!'
            new_instruction, comment = instruction.split('!')
        elif '#' in instruction:
            comment_char = '#'
            new_instruction, comment = instruction.split('#')
        else:
            comment_char = ''
            comment = ''
            new_instruction = instruction
        
        orig_instruction = new_instruction
            
        if not '~' in new_instruction:
            return instruction, None
        
        found = False
        # Check for scenarion stuff
        for key, val in self.scenario_vals.items():
            temp = '~' + key + '~'
            if temp in new_instruction:
                found = True
                new_instruction = new_instruction.replace(temp, val)
                i=0

        # Check for event stuff
        for key, val in self.event_vals.items():
            temp = '~' + key + '~'
            if temp in new_instruction:
                found = True
                new_instruction = new_instruction.replace(temp, val)
        
        if not found:
            return instruction, None
        else:
            orig_instruction = os.path.splitext(orig_instruction)[0]
            return new_instruction + comment_char + comment, orig_instruction
    
    
    def addScenarioVars(self, instruction):
        """Checks the contents of a Model Scenarios == command.
        
        'Model Scenarios ==' commands are overridden by any matching values 
        that are specified when loading the model. This functions checks the
        contents of instruction and updates the global scenario variables IF
        it is not already being used.
        
        Args:
            instruction(str): everything after the '==' in the file line.
        """
        if self.scenario_vals.values(): return
        
        if '!' in instruction:
            instruction = instruction.split('!')
        if '#' in instruction:
            instruction = instruction.split('#')
        
        instruction = instruction.strip()
        
        vals = instruction.split('|')
        for i, v in enumerate(vals):
            key = 's' + str(i+1)
            self.scenario_vals[key] = v
        
    
    def checkForPipes(self, instruction, file_d, hex_hash):
        """Checks the instruction command to see if it contains multiple files.
        
        Some commands in tuflow can contain multiple files seperate by a pipe
        "|" charactar. This checks to see if that's the case and returns the
        file names if so.
        """
        found, split, comment_char = self.separateComment(instruction)
        
        comment = ''
        if found: comment = split[1].strip()
        instruction = split[0].strip()

        instruction = instruction.split('|')
        
        hashes = []
        for i, val in enumerate(instruction):
            hashes.append(file_d.generateHash(hex_hash + val))
        
        if found:
            instruction[-1] = instruction[-1] + ' ' + comment_char + ' ' + comment
        
        return instruction, hashes

    
    def extractExtension(self, instruction):
        """Find the file extension in a file line.
        
        Args:
            instruction(str): the part of the line after the '=='
        
        Returns:
            String - containing the file extension.
        """
        if '!' in instruction:
            instruction = instruction.split('!')[0]
        if '#' in instruction:
            instruction= instruction.split('#')[0]
        root, filename = os.path.split(instruction)
        ext = os.path.splitext(filename)[1][1:]
        ext = ext.strip().lower()
        return ext
    
    
    def separateComment(self, instruction):
        """Separates any comment from the line.
        
        Args:
            instructions(str): the line to seperate comment from.
        
        Return:
            tuple(Bool, tuple) - (0)==True if comment found, (1) contains a 
                tuple with (instruction, comment).
        """
        comment_char = None
        if '!' in instruction:
            comment_char = '!'

        if '#' in instruction:
            comment_char = '#'

        if not comment_char is None:
            split = instruction.split(comment_char, 1)
        else:
            split = [instruction]
            comment_char = ''
    
        if len(split) > 1:
            return True, split, comment_char
        else:
            return False, split, comment_char
        
        
    def _breakLine(self, line):
        """Breaks a file line into it's command/instruction components.
        
        Most lines in tuflow files are in the form::
        Read Command == ..\some\path.ext ! comment
        
        This separates the command from the rest.
        
        Args:
            line(str): the line as read from the file.
        
        Returns:
            Tuple - (command, instruction).
        """
        line = line.strip()
        command, instruction = line.split('==', 1)
        command = command.strip()
        instruction = instruction.strip()
        return command, instruction
    
    
    def _breakAuto(self, line, filename):
        """Estry file 'AUTO' fix.
        
        Estry files can either be loaded with a filepath like the other files
        or with the 'AUTO' feature. This requires only:
        
        :code:'READ ESTRY FILE AUTO'
        
        and means that we won't have a file path stored.
        
        This function replaces the line with the .tcf file path (and .ecf
        extension) so it can be treated like all of the other files.
        
        Note:
            There is functionality in the TuflowModel class to convert this
            back to 'AUTO' when writing.
         
        Args:
            line(str): line as read from the file.
            filename(str): the file path name part.
        """
        if '==' in line:
            line = line.replace(' == ', ' ')
            
        index = line.upper().find('AUTO')
        command = line[:index-1]
        end = line[index+5:]
        filename = os.path.splitext(filename)[0]
        new_line = ' '.join([command, '==', filename + '.ecf', end])
        return new_line

    
    def _resolveResult(self, entry):
        """Fixes the self.type.RESULT paths after load.
        
        Result and Check file paths are a bit tricky because they can be set
        up in a range of ways as either relative or absolute e.g.:
        ::
            ..\some\path\end  
            ..\some\path\end\  
            ..\some\path\end_as_prefix_  
        
        If output is a checkfile no '\' on the end indicates that the final
        string should be prepended to all files, but if it's a result output
        it is the same as having a '\' on the end.
        
        This method attempts to work out what's going on with it all and setup
        the root, relative_root and filename accordingly.
        
        TODO:
            Need to account for the case where a string follows the directory
            is given that will be prepended to all output files.
        
        Args:
            entry(TuflowFilePart): containing a RESULT type.
        
        Returns:
            TuflowFilePart - ammended.
        """
        
        RESULT, CHECK, LOG = range(3)
        if entry.command.upper() == 'OUTPUT FOLDER':
            rtype = RESULT
        elif entry.command.upper() == 'WRITE CHECK FILES':
            rtype = CHECK
        else:
            rtype = LOG
             
        is_absolute = os.path.isabs(entry.path_as_read)
        basename = os.path.basename(entry.path_as_read)
        final_char = entry.path_as_read[-1]
        trailing_slash = final_char == '\\' or final_char == '/'
        
        if is_absolute:
            entry.has_own_root = True
            entry.relative_root = ''
            
            # If there's a slash on the end keep path as it is
            if trailing_slash:
                entry.root = entry.path_as_read
            
            # Get directory for CHECK files so we can set a filename prefix later
            # or stick a slash on the end for the others to make it easier to
            # deal with it later
            elif not trailing_slash and rtype == CHECK:
                entry.root = os.path.dirname(entry.path_as_read) + os.sep
            else:
                entry.root = entry.path_as_read + os.sep
        else:
            # This shouldn't ever happen, but in case it does we set it to 
            # '' here so it doesn't catch anyone out later
            if entry.root is None: entry.root = ''

            entry.has_own_root = False
            if trailing_slash:
                entry.relative_root = entry.path_as_read
            elif not trailing_slash and rtype == CHECK:
                entry.relative_root = os.path.dirname(entry.path_as_read)
            else:
                entry.relative_root = entry.path_as_read + os.sep
                
        entry.file_name = ''
        entry.extension = ''
        entry.file_name_is_prefix = False
        
        # A trailing string is a prefix in check files so set that up here
        if rtype == CHECK:
            if not trailing_slash:
                entry.file_name_is_prefix = True
                entry.file_name = os.path.basename(entry.path_as_read)
       
        return entry
    
    
    def _checkProjectionIsFile(self, instruction):
        """Check that the MI PROJECTION line is a file and not a command.
        
        Args:
            instruction: the instruction command after the ==.
            
        Return:
            Bool - True if it's a file and False otherwise.
        """
        if 'COORDSYS' in instruction.upper():
            return False
        else:
            return True
        
            
    class _FileDetails(object):
        """Protected nested class for storing file load details.
        
        This class stores file load state and data so that we don't need to
        keep passing it back and forth through all of the functions.
        
        Contains methods for loading the file and generating some of the
        model file objects as well.
        """
    
        def __init__(self, details, gen_hash):
            """Initialise class state.
            
            Can either be handed the hash values as element 2 & 3 or details,
            or generate them by setting gen_hash to True. This is because the
            first tcf file will need them generated, but none of the later
            ones will.
            """
            self.path = details[0]
            self.root, self.filename = os.path.split(self.path)
            self.extension = os.path.splitext(self.filename)[1][1:]
            if gen_hash:
#                 self.head_hash = self.generateHash(self.filename + 'HEAD')
                self.head_hash = self.generateModelfileHash(self.path)
                self.parent_hash = None
            else:
                self.head_hash = details[1]
                self.parent_hash = details[2]
            self.relative_root = details[3]


        def getModel(self):
            """Generates a new TuflowModelFile base on current state.
            
            Returns:
                TuflowModelFile - based on extension in class.
            """
            return TuflowModelFile(self.extension, self.head_hash, 
                                   self.parent_hash, self.filename) 
            

        def generateModelTuflowFile(self, line_type, global_order, 
                                    modelfile_type=None):
            """Creates a TuflowFile object from the current state.
            
            Generates the class the self params.
            
            Args:
                line_type(int): one of the TuflowLoader.types value e.g. MODEL
            
            Returns:
                TuflowFile object.
            """
            return tfp.ModelFile(global_order, self.filename, self.head_hash, 
                        line_type, self.extension, modelfile_type, self.root, 
                        self.relative_root, self.extension)
        
        
        def generateModelfileHash(self, absolute_path): 
            """Generate an md5sum hashcode for a model file.
            
            Uses the absolute path of the model file to generate the hash code.
            This way when the same file is read twice it will always produce 
            the same hashcode and we won't need to create two different 
            TuflowModelFile's for the same control file.
            
            In order to do this the path is normalised before hashing so that
            any difference in the relative path structure is removed.
            
            Args:
                absolute_path(str): the absolute path to the model file.
            
            Return:
                str - hexidecimal hashcode created from the path.
            """
            salt = os.path.normpath(absolute_path)
            head_hash = hashlib.md5(salt.encode())
            head_hash = head_hash.hexdigest()
            return head_hash
            

        def generateHash(self, salt, add_random=True):
            """Generate an md5sum hashcode from the salt.
             
            Md5 should be good enough for the number of them we will be 
            generating.
             
            Args:
                salt: string to use to generate the hash value.
                 
            Returns:
                Hexadecimal version of the hash value.
            """
            if add_random:
                salt += str(random.randint(-500, 500))
            head_hash = hashlib.md5(salt.encode())
            head_hash = head_hash.hexdigest()
            return head_hash
        
        
        def getFile(self):
            """Load the file into the contents list.

            Args:
                file_path (str): path to the required file. 
            
            Returns:
                True if loaded ok, False otherwise.
            """
            logger.debug('loading File: ' + self.path)
            try: 
                self.raw_contents = filetools.getFile(self.path)
            except IOError: 
                logger.error('IOError - Unable to load file')
                return False
                
            if(self.raw_contents == None): 
                logger.error('model file is empty at: ' + self.path)
                return False
                    
            return True


